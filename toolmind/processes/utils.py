"""
Asynchronous Task Processor - Optimized Version
Provides high-performance concurrent task execution capabilities, including progress bar display, error retry, and resource management features.
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
    """Progress bar configuration"""
    enabled: bool = True              # Enable/disable progress bar
    min_interval: float = 0.1         # Minimum update interval in seconds
    ncols: int = 80                   # Terminal width for progress bar
    leave: bool = True                # Keep progress bar after completion
    show_summary: bool = True         # Show completion summary
    show_errors: bool = True          # Show error count in progress bar


class ProgressBar:
    """Enhanced progress bar implementation with better visual clarity"""
    
    def __init__(self, total: int, desc: str = "", config: Optional[ProgressConfig] = None):
        self.config = config or ProgressConfig()
        self.total = total
        self.desc = desc or "Processing"
        self.current = 0
        self.start_time = time.time()
        self.last_update = 0
        self._closed = False
        self.success_count = 0
        self.error_count = 0
        
        if self.config.enabled and total > 0:
            self._draw()
    
    def update(self, n: int = 1, success: bool = True) -> None:
        """Update progress with success/error tracking"""
        if not self.config.enabled or self._closed:
            return
            
        current_time = time.time()
        self.current = min(self.current + n, self.total)
        
        # Track success/error counts
        if success:
            self.success_count += n
        else:
            self.error_count += n
        
        # Rate limiting for updates
        if (current_time - self.last_update >= self.config.min_interval or 
            self.current >= self.total):
            self.last_update = current_time
            self._draw()
    
    def _draw(self) -> None:
        """Draw enhanced progress bar with better visual elements"""
        if not self.config.enabled:
            return
            
        try:
            elapsed = time.time() - self.start_time
            percent = 100.0 * self.current / self.total if self.total > 0 else 100.0
            
            # Calculate progress bar with better visual elements
            bar_width = max(25, self.config.ncols - len(self.desc) - 55)
            filled = int(bar_width * self.current / self.total) if self.total > 0 else bar_width
            
            # Enhanced progress bar with gradient effect
            if filled == bar_width:
                bar = '█' * filled
            elif filled > 0:
                bar = '█' * (filled - 1) + '▓' + '░' * (bar_width - filled)
            else:
                bar = '░' * bar_width
            
            # Calculate speed and ETA
            if elapsed > 0 and self.current > 0:
                rate = self.current / elapsed
                eta = (self.total - self.current) / rate if rate > 0 else 0
                rate_str = f"{rate:.1f}/s"
                eta_str = self._format_time(eta)
            else:
                rate_str = "-.--/s"
                eta_str = "--:--"
            
            # Status indicators
            status_color = ""
            if self.error_count > 0:
                error_rate = (self.error_count / self.current) * 100 if self.current > 0 else 0
                if error_rate > 10:
                    status_color = "⚠️ "
                elif error_rate > 0:
                    status_color = "⚡"
            elif self.current == self.total:
                status_color = "✅"
            else:
                status_color = "🔄"
            
            # Build enhanced output
            elapsed_str = self._format_time(elapsed)
            
            # Main progress line
            progress_line = (
                f"\r{status_color} {self.desc}: {percent:5.1f}% |{bar}| "
                f"{self.current:,}/{self.total:,} [{elapsed_str}<{eta_str}, {rate_str}]"
            )
            
            # Add error info if any and if enabled
            if self.error_count > 0 and self.config.show_errors:
                progress_line += f" [❌{self.error_count}]"
            
            # Ensure line doesn't exceed terminal width
            if len(progress_line) > self.config.ncols:
                progress_line = progress_line[:self.config.ncols-3] + "..."
            
            # Output with padding to clear previous line
            sys.stdout.write(progress_line.ljust(self.config.ncols))
            sys.stdout.flush()
            
        except Exception as e:
            logger.warning(f"Progress bar update failed: {e}")
            self.config.enabled = False
    
    def _format_time(self, seconds: float) -> str:
        """Format time display with better precision"""
        if seconds < 0:
            return "--:--"
        elif seconds < 60:
            return f"{seconds:4.1f}s"
        elif seconds < 3600:
            mins, secs = divmod(int(seconds), 60)
            return f"{mins:2d}:{secs:02d}"
        else:
            hours, remainder = divmod(int(seconds), 3600)
            mins, _ = divmod(remainder, 60)
            return f"{hours}:{mins:02d}h"
    
    def close(self) -> None:
        """Close progress bar with final status"""
        if self._closed:
            return
            
        if self.config.enabled:
            if self.config.leave:
                # Final draw with completion status
                self._draw()
                
                # Add summary line if enabled
                if self.config.show_summary:
                    elapsed = time.time() - self.start_time
                    if self.error_count == 0:
                        summary = f"\n✅ Completed {self.total:,} tasks in {self._format_time(elapsed)}"
                    else:
                        success_rate = ((self.total - self.error_count) / self.total) * 100
                        summary = f"\n⚠️  Completed {self.total:,} tasks in {self._format_time(elapsed)} (Success: {success_rate:.1f}%)"
                    
                    sys.stdout.write(summary + '\n')
                else:
                    sys.stdout.write('\n')  # Just add a newline
            else:
                # Clear the line
                sys.stdout.write('\r' + ' ' * self.config.ncols + '\r')
            sys.stdout.flush()
        
        self._closed = True
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class TaskExecutor:
    """Individual task executor with retry logic and type-specific optimization"""
    
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
        """Execute single task with retry logic and type-specific optimization"""
        async with semaphore:
            for attempt in range(self.retry_config.max_retries + 1):
                try:
                    if is_async:
                        result = await func(*args)
                    else:
                        # Select executor based on task type
                        executor = self._get_executor(task_type)
                        loop = asyncio.get_running_loop()
                        
                        # For process pool, need serializable functions
                        if isinstance(executor, concurrent.futures.ProcessPoolExecutor):
                            # Use global function to avoid serialization issues
                            import functools
                            callable_func = functools.partial(_execute_func_wrapper, func, args)
                            result = await loop.run_in_executor(executor, callable_func)
                        else:
                            result = await loop.run_in_executor(executor, lambda: func(*args))
                    
                    return task_id, result
                    
                except Exception as e:
                    if attempt == self.retry_config.max_retries:
                        logger.error(f"Task {task_id} failed permanently: {e}")
                        return task_id, None
                    
                    # Calculate backoff delay
                    delay = self._calculate_delay(attempt)
                    logger.warning(f"Task {task_id} attempt {attempt + 1} failed, retrying in {delay:.2f}s: {e}")
                    await asyncio.sleep(delay)
        
        return task_id, None
    
    def _get_executor(self, task_type: TaskType):
        """Get appropriate executor based on task type"""
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
            # For network-intensive and mixed tasks, use default async execution
            return None
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate retry delay with exponential backoff"""
        delay = min(
            self.retry_config.base_delay * (self.retry_config.backoff_factor ** attempt),
            self.retry_config.max_delay
        )
        
        if self.retry_config.jitter:
            delay *= (0.5 + 0.5 * random.random())
        
        return delay


class SessionManager:
    """HTTP session manager with optimized connection pooling"""
    
    def __init__(self, config: ConcurrencyConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
    
    @asynccontextmanager
    async def get_session(self):
        """Get HTTP session context manager with optimized settings"""
        session_created = False
        try:
            if self._session is None:
                # Optimized connector configuration for network-intensive tasks
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
        """Clean up session resources"""
        if self._session:
            await self._session.close()
            self._session = None


class AsyncTaskProcessor:
    """Optimized async task processor with type-specific execution strategies"""
    
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
        """Get or create semaphore for concurrency control"""
        # Determine default max_workers based on task type
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
        """Process tasks in batch with optimized concurrency control"""
        if not task_list:
            return []
        
        # Parameter validation and adjustment
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
            1000  # Hard limit for safety
        )
        
        is_async = inspect.iscoroutinefunction(func)
        semaphore = self._get_semaphore(task_type, max_workers)
        
        # Create tasks
        tasks = []
        for i, args in enumerate(task_list):
            task = asyncio.create_task(
                self.task_executor.execute_with_retry(
                    func, args, i, semaphore, task_type, is_async
                )
            )
            tasks.append(task)
        
        # Process tasks with progress tracking
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
            
            # Build result list (maintain original order)
            return [results_dict.get(i) for i in range(len(task_list))]
            
        finally:
            if progress_bar:
                progress_bar.close()
            
            # Cancel unfinished tasks
            await self._cleanup_tasks(tasks)
    
    async def _process_tasks_with_progress(
        self, 
        tasks: List[asyncio.Task], 
        progress_bar: Optional[ProgressBar]
    ) -> AsyncIterator[TaskResult]:
        """Process tasks and update progress with success/failure tracking"""
        completed = 0
        
        for task in asyncio.as_completed(tasks):
            try:
                result = await task
                completed += 1
                
                # Check if task was successful (result is not None)
                task_id, task_result = result
                success = task_result is not None
                
                if progress_bar:
                    progress_bar.update(1, success=success)
                
                yield result
                
            except Exception as e:
                logger.error(f"Task execution error: {e}")
                completed += 1
                if progress_bar:
                    progress_bar.update(1, success=False)
    
    async def _cleanup_tasks(self, tasks: List[asyncio.Task]) -> None:
        """Clean up unfinished tasks"""
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as e:
                    logger.warning(f"Task cleanup error: {e}")
    
    def submit(
        self,
        func: Callable,
        task_list: List[TaskInput],
        name: str = "",
        max_workers: Optional[int] = None,
        show_progress: bool = True,
        task_type: TaskType = TaskType.MIXED
    ) -> List[Any]:
        """Submit tasks synchronously (main entry point)"""
        try:
            # Try to get current event loop
            loop = asyncio.get_running_loop()
            # If in running loop, run directly
            return loop.run_until_complete(
                self.process_batch(func, task_list, name, max_workers, show_progress, task_type)
            )
        except RuntimeError:
            # If no running loop, create new one
            return asyncio.run(
                self.process_batch(func, task_list, name, max_workers, show_progress, task_type)
            )
    
    async def __aenter__(self):
        """Async context manager entry"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        # Clean up resources
        await self.cleanup()
    
    async def cleanup(self):
        """Clean up all resources"""
        # Clean up session
        await self.session_manager.cleanup()
        
        # Clean up executor resources
        if hasattr(self.task_executor, '_thread_pool') and self.task_executor._thread_pool:
            self.task_executor._thread_pool.shutdown(wait=True)
        
        if hasattr(self.task_executor, '_process_pool') and self.task_executor._process_pool:
            self.task_executor._process_pool.shutdown(wait=True)
        
        # Clear semaphores
        self._semaphores.clear()


# Convenience functions and backward compatibility
class AsyncTasks(AsyncTaskProcessor):
    """Backward compatible class name"""
    pass


def create_task_processor(
    max_workers: int = 50,
    max_retries: int = 3,
    show_progress: bool = True,
    show_summary: bool = True,
    show_errors: bool = True
) -> AsyncTaskProcessor:
    """Create general-purpose task processor with configurable progress display"""
    concurrency_config = ConcurrencyConfig(max_workers=max_workers)
    retry_config = RetryConfig(max_retries=max_retries)
    progress_config = ProgressConfig(
        enabled=show_progress,
        show_summary=show_summary,
        show_errors=show_errors
    )
    
    return AsyncTaskProcessor(
        concurrency_config=concurrency_config,
        retry_config=retry_config,
        progress_config=progress_config
    )


def create_io_processor(
    max_workers: int = 100,
    thread_pool_size: int = 20,
    max_retries: int = 3,
    show_progress: bool = True,
    show_summary: bool = True,
    show_errors: bool = True
) -> AsyncTaskProcessor:
    """Create IO-intensive task processor with thread pool optimization"""
    concurrency_config = ConcurrencyConfig(
        max_workers=max_workers,
        io_max_workers=max_workers,
        io_thread_pool_size=thread_pool_size
    )
    retry_config = RetryConfig(max_retries=max_retries)
    progress_config = ProgressConfig(
        enabled=show_progress,
        show_summary=show_summary,
        show_errors=show_errors
    )
    
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
    show_progress: bool = True,
    show_summary: bool = True,
    show_errors: bool = True
) -> AsyncTaskProcessor:
    """Create network-intensive task processor with connection pool optimization"""
    concurrency_config = ConcurrencyConfig(
        max_workers=max_workers,
        network_max_workers=max_workers,
        connection_limit=connection_limit,
        network_timeout=timeout,
        network_read_timeout=timeout * 2
    )
    retry_config = RetryConfig(
        max_retries=max_retries,
        base_delay=0.5,  # Network errors need longer retry intervals
        backoff_factor=2.0
    )
    progress_config = ProgressConfig(
        enabled=show_progress,
        show_summary=show_summary,
        show_errors=show_errors
    )
    
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