"""
异步任务处理器 - 优化版本
提供高性能的并发任务执行能力，包含进度条显示、错误重试、资源管理等功能
"""

import asyncio
import logging
import sys
import time
import random
import inspect
import os
import concurrent.futures
from dataclasses import dataclass, field
from typing import (
    Callable, List, Any, Dict, Tuple, Optional, Union, 
    AsyncIterator, Coroutine, TypeVar, Generic
)
from contextlib import asynccontextmanager
from functools import wraps
from enum import Enum

import nest_asyncio
import aiohttp

# 启用嵌套事件循环支持
nest_asyncio.apply()

# 类型定义
T = TypeVar('T')
TaskResult = Tuple[int, Optional[T]]
TaskInput = List[Any]


def _execute_func_wrapper(func, args):
    """可序列化的函数包装器，用于ProcessPoolExecutor"""
    return func(*args)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(Enum):
    """任务类型枚举"""
    IO_INTENSIVE = "io_intensive"      # IO密集型任务
    NETWORK_INTENSIVE = "network_intensive"  # 网络密集型任务
    CPU_INTENSIVE = "cpu_intensive"    # CPU密集型任务
    MIXED = "mixed"                    # 混合型任务


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3
    base_delay: float = 0.1
    backoff_factor: float = 1.5
    max_delay: float = 60.0
    jitter: bool = True


@dataclass
class ConcurrencyConfig:
    """并发配置"""
    max_workers: int = 50
    connection_limit: int = 100
    keepalive_timeout: int = 60
    enable_ssl: bool = False
    
    # IO密集型任务配置
    io_thread_pool_size: int = 20
    io_max_workers: int = 100
    
    # 网络密集型任务配置
    network_max_workers: int = 200
    network_timeout: float = 30.0
    network_read_timeout: float = 60.0
    
    # CPU密集型任务配置
    cpu_process_pool_size: int = None  # None表示使用CPU核心数


@dataclass
class ProgressConfig:
    """进度条配置"""
    enabled: bool = True
    min_interval: float = 0.1
    ncols: int = 80
    leave: bool = True


class ProgressBar:
    """优化的进度条实现"""
    
    def __init__(self, total: int, desc: str = "", config: Optional[ProgressConfig] = None):
        self.config = config or ProgressConfig()
        self.total = total
        self.desc = desc
        self.current = 0
        self.start_time = time.time()
        self.last_update = 0
        self._closed = False
        
        if self.config.enabled and total > 0:
            self._draw()
    
    def update(self, n: int = 1) -> None:
        """更新进度"""
        if not self.config.enabled or self._closed:
            return
            
        current_time = time.time()
        self.current = min(self.current + n, self.total)
        
        # 限制更新频率
        if (current_time - self.last_update >= self.config.min_interval or 
            self.current >= self.total):
            self.last_update = current_time
            self._draw()
    
    def _draw(self) -> None:
        """绘制进度条"""
        if not self.config.enabled:
            return
            
        try:
            elapsed = time.time() - self.start_time
            percent = 100.0 * self.current / self.total if self.total > 0 else 100.0
            
            # 计算进度条
            bar_width = max(20, self.config.ncols - len(self.desc) - 40)
            filled = int(bar_width * self.current / self.total) if self.total > 0 else bar_width
            bar = '█' * filled + '░' * (bar_width - filled)
            
            # 计算速度和估计时间
            if elapsed > 0 and self.current > 0:
                rate = self.current / elapsed
                eta = (self.total - self.current) / rate if rate > 0 else 0
                rate_str = f"{rate:.1f}it/s"
                eta_str = self._format_time(eta)
            else:
                rate_str = "?it/s"
                eta_str = "?"
            
            # 构建输出
            elapsed_str = self._format_time(elapsed)
            progress_line = (
                f"\r{self.desc}: {percent:5.1f}%|{bar}| "
                f"{self.current}/{self.total} [{elapsed_str}<{eta_str}, {rate_str}]"
            )
            
            # 输出
            sys.stdout.write(progress_line)
            sys.stdout.flush()
            
        except Exception as e:
            logger.warning(f"进度条更新失败: {e}")
            self.config.enabled = False
    
    def _format_time(self, seconds: float) -> str:
        """格式化时间显示"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            return f"{seconds/60:.1f}min"
        else:
            return f"{seconds/3600:.1f}h"
    
    def close(self) -> None:
        """关闭进度条"""
        if self._closed:
            return
            
        if self.config.enabled:
            if self.config.leave:
                self._draw()
                sys.stdout.write('\n')
            else:
                sys.stdout.write('\r' + ' ' * self.config.ncols + '\r')
            sys.stdout.flush()
        
        self._closed = True
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class TaskExecutor:
    """单一任务执行器"""
    
    def __init__(self, retry_config: RetryConfig, concurrency_config: ConcurrencyConfig):
        self.retry_config = retry_config
        self.concurrency_config = concurrency_config
        self._thread_pool = None
        self._process_pool = None
    
    async def execute_with_retry(
        self, 
        func: Callable,
        args: List[Any],
        task_id: int,
        semaphore: asyncio.Semaphore,
        task_type: TaskType = TaskType.MIXED,
        is_async: bool = False
    ) -> TaskResult[Any]:
        """执行单个任务，包含重试逻辑"""
        async with semaphore:
            for attempt in range(self.retry_config.max_retries + 1):
                try:
                    if is_async:
                        result = await func(*args)
                    else:
                        # 根据任务类型选择执行器
                        executor = self._get_executor(task_type)
                        loop = asyncio.get_running_loop()
                        
                        # 对于进程池，需要可序列化的函数
                        if isinstance(executor, concurrent.futures.ProcessPoolExecutor):
                            # 使用全局函数避免序列化问题
                            import functools
                            callable_func = functools.partial(_execute_func_wrapper, func, args)
                            result = await loop.run_in_executor(executor, callable_func)
                        else:
                            result = await loop.run_in_executor(executor, lambda: func(*args))
                    
                    return task_id, result
                    
                except Exception as e:
                    if attempt == self.retry_config.max_retries:
                        logger.error(f"任务 {task_id} 最终失败: {e}")
                        return task_id, None
                    
                    # 计算退避延迟
                    delay = self._calculate_delay(attempt)
                    logger.warning(f"任务 {task_id} 第 {attempt + 1} 次尝试失败，{delay:.2f}s后重试: {e}")
                    await asyncio.sleep(delay)
        
        return task_id, None
    
    def _get_executor(self, task_type: TaskType):
        """根据任务类型获取合适的执行器"""
        if task_type == TaskType.IO_INTENSIVE:
            if self._thread_pool is None:
                self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.concurrency_config.io_thread_pool_size
                )
            return self._thread_pool
        elif task_type == TaskType.CPU_INTENSIVE:
            if self._process_pool is None:
                max_workers = (
                    self.concurrency_config.cpu_process_pool_size 
                    or os.cpu_count()
                )
                self._process_pool = concurrent.futures.ProcessPoolExecutor(
                    max_workers=max_workers
                )
            return self._process_pool
        else:
            # 对于网络密集型和混合型任务，使用默认的线程池
            return None
    
    def _calculate_delay(self, attempt: int) -> float:
        """计算重试延迟"""
        delay = min(
            self.retry_config.base_delay * (self.retry_config.backoff_factor ** attempt),
            self.retry_config.max_delay
        )
        
        if self.retry_config.jitter:
            delay *= (0.5 + 0.5 * random.random())
        
        return delay


class SessionManager:
    """HTTP会话管理器"""
    
    def __init__(self, config: ConcurrencyConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
    
    @asynccontextmanager
    async def get_session(self):
        """获取HTTP会话的上下文管理器"""
        session_created = False
        try:
            if self._session is None:
                # 为网络密集型任务优化的连接器配置
                timeout = aiohttp.ClientTimeout(
                    total=self.config.network_timeout,
                    connect=self.config.network_timeout / 3,
                    sock_read=self.config.network_read_timeout
                )
                
                connector = aiohttp.TCPConnector(
                    limit=self.config.connection_limit,
                    limit_per_host=50,  # 每个主机的连接限制
                    keepalive_timeout=self.config.keepalive_timeout,
                    ssl=self.config.enable_ssl,
                    ttl_dns_cache=300,  # DNS缓存5分钟
                    use_dns_cache=True,
                    enable_cleanup_closed=True
                )
                
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout
                )
                session_created = True
            
            yield self._session
            
        finally:
            if session_created and self._session:
                await self._session.close()
                self._session = None
    
    async def cleanup(self):
        """清理会话资源"""
        if self._session:
            await self._session.close()
            self._session = None


class AsyncTaskProcessor:
    """优化的异步任务处理器"""
    
    def __init__(
        self,
        concurrency_config: Optional[ConcurrencyConfig] = None,
        retry_config: Optional[RetryConfig] = None,
        progress_config: Optional[ProgressConfig] = None
    ):
        self.concurrency_config = concurrency_config or ConcurrencyConfig()
        self.retry_config = retry_config or RetryConfig()
        self.progress_config = progress_config or ProgressConfig()
        
        self.session_manager = SessionManager(self.concurrency_config)
        self.task_executor = TaskExecutor(self.retry_config, self.concurrency_config)
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
    
    def _get_semaphore(self, task_type: TaskType, max_workers: Optional[int] = None) -> asyncio.Semaphore:
        """获取或创建信号量"""
        # 根据任务类型确定默认的max_workers
        if max_workers is None:
            if task_type == TaskType.IO_INTENSIVE:
                max_workers = self.concurrency_config.io_max_workers
            elif task_type == TaskType.NETWORK_INTENSIVE:
                max_workers = self.concurrency_config.network_max_workers
            else:
                max_workers = self.concurrency_config.max_workers
        
        semaphore_key = f"{task_type.value}_{max_workers}"
        if semaphore_key not in self._semaphores:
            self._semaphores[semaphore_key] = asyncio.Semaphore(max_workers)
        return self._semaphores[semaphore_key]
    
    async def process_batch(
        self,
        func: Callable,
        task_list: List[TaskInput],
        name: str = "",
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        task_type: TaskType = TaskType.MIXED
    ) -> List[Any]:
        """批量处理任务"""
        if not task_list:
            return []
        
        # 参数校验和调整
        if max_workers is None:
            if task_type == TaskType.IO_INTENSIVE:
                default_workers = self.concurrency_config.io_max_workers
            elif task_type == TaskType.NETWORK_INTENSIVE:
                default_workers = self.concurrency_config.network_max_workers
            else:
                default_workers = self.concurrency_config.max_workers
        else:
            default_workers = max_workers
            
        max_workers = min(
            default_workers,
            len(task_list),
            1000  # 硬限制
        )
        
        is_async = inspect.iscoroutinefunction(func)
        semaphore = self._get_semaphore(task_type, max_workers)
        
        # 创建任务
        tasks = []
        for i, args in enumerate(task_list):
            task = asyncio.create_task(
                self.task_executor.execute_with_retry(
                    func, args, i, semaphore, task_type, is_async
                )
            )
            tasks.append(task)
        
        # 处理任务并显示进度
        results_dict: Dict[int, Any] = {}
        
        progress_bar = None
        if show_progress and self.progress_config.enabled:
            progress_bar = ProgressBar(
                total=len(tasks),
                desc=name or func.__name__,
                config=self.progress_config
            )
        
        try:
            async for task_result in self._process_tasks_with_progress(tasks, progress_bar):
                task_id, result = task_result
                results_dict[task_id] = result
            
            # 构建结果列表（保持原始顺序）
            return [results_dict.get(i) for i in range(len(task_list))]
            
        finally:
            if progress_bar:
                progress_bar.close()
            
            # 取消未完成的任务
            await self._cleanup_tasks(tasks)
    
    async def _process_tasks_with_progress(
        self, 
        tasks: List[asyncio.Task], 
        progress_bar: Optional[ProgressBar]
    ) -> AsyncIterator[TaskResult]:
        """处理任务并更新进度"""
        completed = 0
        
        for task in asyncio.as_completed(tasks):
            try:
                result = await task
                completed += 1
                
                if progress_bar:
                    progress_bar.update(1)
                
                yield result
                
            except Exception as e:
                logger.error(f"任务执行异常: {e}")
                completed += 1
                if progress_bar:
                    progress_bar.update(1)
    
    async def _cleanup_tasks(self, tasks: List[asyncio.Task]) -> None:
        """清理未完成的任务"""
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as e:
                    logger.warning(f"任务清理异常: {e}")
    
    def submit(
        self,
        func: Callable,
        task_list: List[TaskInput],
        name: str = "",
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        task_type: TaskType = TaskType.MIXED
    ) -> List[Any]:
        """同步提交任务（主要入口点）"""
        try:
            # 尝试获取当前事件循环
            loop = asyncio.get_running_loop()
            # 如果在运行中的循环中，直接运行
            return loop.run_until_complete(
                self.process_batch(func, task_list, name, max_workers, show_progress, task_type)
            )
        except RuntimeError:
            # 如果没有运行的循环，创建新的
            return asyncio.run(
                self.process_batch(func, task_list, name, max_workers, show_progress, task_type)
            )
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        # 清理资源
        await self.cleanup()
    
    async def cleanup(self):
        """清理所有资源"""
        # 清理会话
        await self.session_manager.cleanup()
        
        # 清理执行器资源
        if hasattr(self.task_executor, '_thread_pool') and self.task_executor._thread_pool:
            self.task_executor._thread_pool.shutdown(wait=True)
        
        if hasattr(self.task_executor, '_process_pool') and self.task_executor._process_pool:
            self.task_executor._process_pool.shutdown(wait=True)
        
        # 清理信号量
        self._semaphores.clear()


# 便利函数和向后兼容性
class AsyncTasks(AsyncTaskProcessor):
    """保持向后兼容的类名"""
    pass


def create_task_processor(
    max_workers: int = 50,
    max_retries: int = 3,
    show_progress: bool = True
) -> AsyncTaskProcessor:
    """创建任务处理器的便利函数"""
    concurrency_config = ConcurrencyConfig(max_workers=max_workers)
    retry_config = RetryConfig(max_retries=max_retries)
    progress_config = ProgressConfig(enabled=show_progress)
    
    return AsyncTaskProcessor(
        concurrency_config=concurrency_config,
        retry_config=retry_config,
        progress_config=progress_config
    )


def create_io_processor(
    max_workers: int = 100,
    thread_pool_size: int = 20,
    max_retries: int = 3,
    show_progress: bool = True
) -> AsyncTaskProcessor:
    """创建IO密集型任务处理器"""
    concurrency_config = ConcurrencyConfig(
        max_workers=max_workers,
        io_max_workers=max_workers,
        io_thread_pool_size=thread_pool_size
    )
    retry_config = RetryConfig(max_retries=max_retries)
    progress_config = ProgressConfig(enabled=show_progress)
    
    return AsyncTaskProcessor(
        concurrency_config=concurrency_config,
        retry_config=retry_config,
        progress_config=progress_config
    )


def create_network_processor(
    max_workers: int = 200,
    connection_limit: int = 500,
    timeout: float = 30.0,
    max_retries: int = 5,
    show_progress: bool = True
) -> AsyncTaskProcessor:
    """创建网络密集型任务处理器"""
    concurrency_config = ConcurrencyConfig(
        max_workers=max_workers,
        network_max_workers=max_workers,
        connection_limit=connection_limit,
        network_timeout=timeout,
        network_read_timeout=timeout * 2
    )
    retry_config = RetryConfig(
        max_retries=max_retries,
        base_delay=0.5,  # 网络错误需要更长的重试间隔
        backoff_factor=2.0
    )
    progress_config = ProgressConfig(enabled=show_progress)
    
    return AsyncTaskProcessor(
        concurrency_config=concurrency_config,
        retry_config=retry_config,
        progress_config=progress_config
    )


# 测试函数
def test(a: int = 0, b: int = 0) -> Tuple[int, int]:
    """测试函数"""
    return a + b, a - b


def test_io_task(filename: str) -> str:
    """模拟IO密集型任务"""
    import os
    time.sleep(0.1)  # 模拟IO延迟
    return f"处理文件: {filename}, 大小: {len(filename)}"


def test_network_task(url: str) -> str:
    """模拟网络请求任务"""
    time.sleep(0.05)  # 模拟网络延迟
    return f"请求URL: {url}, 状态: 200"


if __name__ == "__main__":
    print("开始测试优化后的异步任务处理器...")
    
    # 测试基本功能
    print("\n=== 测试基本功能 ===")
    test_args = [[1, 2], [3, 4], [5, 6]] * 5
    processor = create_task_processor(max_workers=5, max_retries=2)
    
    start_time = time.time()
    results = processor.submit(test, test_args, name="基本测试", task_type=TaskType.MIXED)
    execution_time = time.time() - start_time
    
    print(f"基本任务执行完成，耗时: {execution_time:.2f}秒")
    print(f"结果数量: {len(results)}")
    print(f"前3个结果: {results[:3]}")
    
    # 测试IO密集型任务
    print("\n=== 测试IO密集型任务 ===")
    io_args = [[f"file_{i}.txt"] for i in range(20)]
    io_processor = create_io_processor(max_workers=50, thread_pool_size=10)
    
    start_time = time.time()
    io_results = io_processor.submit(
        test_io_task, 
        io_args, 
        name="IO任务", 
        task_type=TaskType.IO_INTENSIVE
    )
    execution_time = time.time() - start_time
    
    print(f"IO任务执行完成，耗时: {execution_time:.2f}秒")
    print(f"结果数量: {len(io_results)}")
    print(f"前3个结果: {io_results[:3]}")
    
    # 测试网络密集型任务
    print("\n=== 测试网络密集型任务 ===")
    network_args = [[f"https://api.example.com/data/{i}"] for i in range(30)]
    network_processor = create_network_processor(max_workers=100, connection_limit=200)
    
    start_time = time.time()
    network_results = network_processor.submit(
        test_network_task, 
        network_args, 
        name="网络任务", 
        task_type=TaskType.NETWORK_INTENSIVE
    )
    execution_time = time.time() - start_time
    
    print(f"网络任务执行完成，耗时: {execution_time:.2f}秒")
    print(f"结果数量: {len(network_results)}")
    print(f"前3个结果: {network_results[:3]}")
    
    print("\n所有测试完成！")