import threading
import time
import random
from typing import List, Dict, Optional, Tuple, Any
from enum import Enum
from dataclasses import dataclass
from collections import defaultdict
import requests
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LoadBalanceStrategy(Enum):
    """负载均衡策略枚举"""
    ROUND_ROBIN = "round_robin"      # 轮询
    WEIGHTED = "weighted"            # 权重
    LEAST_CONNECTIONS = "least_connections"  # 最少连接
    RANDOM = "random"                # 随机
    HEALTH_FIRST = "health_first"    # 健康优先


@dataclass
class ApiKeyConfig:
    """API密钥配置"""
    api_key: str
    base_url: str
    weight: int = 1                  # 权重，用于权重负载均衡
    max_concurrent: int = 100        # 最大并发数
    timeout: float = 30.0            # 超时时间
    tpm: int = 1000000               # 每分钟最大令牌数 (Tokens Per Minute) - 100万
    rpm: int = 1000                  # 每分钟最大请求数 (Requests Per Minute) - 1000
    is_healthy: bool = True          # 健康状态
    last_health_check: float = 0.0   # 上次健康检查时间
    current_connections: int = 0      # 当前连接数
    total_requests: int = 0          # 总请求数
    failed_requests: int = 0         # 失败请求数
    response_times: List[float] = None  # 响应时间记录
    # 速率限制追踪
    request_timestamps: List[float] = None  # 请求时间戳记录
    token_usage_timestamps: List[Tuple[float, int]] = None  # 令牌使用记录 (时间戳, 令牌数)

    def __post_init__(self):
        if self.response_times is None:
            self.response_times = []
        if self.request_timestamps is None:
            self.request_timestamps = []
        if self.token_usage_timestamps is None:
            self.token_usage_timestamps = []

    @property
    def success_rate(self) -> float:
        """计算成功率"""
        if self.total_requests == 0:
            return 1.0
        return (self.total_requests - self.failed_requests) / self.total_requests

    @property
    def avg_response_time(self) -> float:
        """计算平均响应时间"""
        if not self.response_times:
            return 0.0
        return sum(self.response_times[-10:]) / len(self.response_times[-10:])  # 只看最近10次

    def can_handle_request(self, estimated_tokens: int = 0) -> bool:
        """检查是否可以处理新请求（考虑RPM和TPM限制）"""
        current_time = time.time()
        one_minute_ago = current_time - 60

        # 清理过期的时间戳记录
        self._cleanup_expired_records(one_minute_ago)

        # 检查RPM限制
        recent_requests = len(self.request_timestamps)
        if recent_requests >= self.rpm:
            return False

        # 检查TPM限制
        if estimated_tokens > 0:
            recent_tokens = sum(tokens for _, tokens in self.token_usage_timestamps)
            if recent_tokens + estimated_tokens > self.tpm:
                return False

        return True

    def record_request(self, tokens_used: int = 0) -> None:
        """记录请求和令牌使用"""
        current_time = time.time()
        
        # 记录请求时间戳
        self.request_timestamps.append(current_time)
        
        # 记录令牌使用
        if tokens_used > 0:
            self.token_usage_timestamps.append((current_time, tokens_used))

    def _cleanup_expired_records(self, cutoff_time: float) -> None:
        """清理过期的记录"""
        # 清理请求时间戳
        self.request_timestamps = [ts for ts in self.request_timestamps if ts > cutoff_time]
        
        # 清理令牌使用记录
        self.token_usage_timestamps = [
            (ts, tokens) for ts, tokens in self.token_usage_timestamps if ts > cutoff_time
        ]

    @property
    def current_rpm_usage(self) -> int:
        """获取当前RPM使用情况"""
        current_time = time.time()
        one_minute_ago = current_time - 60
        self._cleanup_expired_records(one_minute_ago)
        return len(self.request_timestamps)

    @property
    def current_tpm_usage(self) -> int:
        """获取当前TPM使用情况"""
        current_time = time.time()
        one_minute_ago = current_time - 60
        self._cleanup_expired_records(one_minute_ago)
        return sum(tokens for _, tokens in self.token_usage_timestamps)

    @property
    def rpm_utilization(self) -> float:
        """RPM利用率（0-1）"""
        return self.current_rpm_usage / self.rpm if self.rpm > 0 else 0.0

    @property
    def tpm_utilization(self) -> float:
        """TPM利用率（0-1）"""
        return self.current_tpm_usage / self.tpm if self.tpm > 0 else 0.0


class ApiKeyManager:
    """API密钥负载均衡管理器"""
    
    def __init__(self, 
                 load_balance_strategy: LoadBalanceStrategy = LoadBalanceStrategy.ROUND_ROBIN,
                 health_check_interval: float = 60.0,
                 health_check_timeout: float = 5.0,
                 enable_health_check: bool = True):
        """
        初始化API密钥管理器
        
        Args:
            load_balance_strategy: 负载均衡策略
            health_check_interval: 健康检查间隔（秒）
            health_check_timeout: 健康检查超时时间（秒）
            enable_health_check: 是否启用健康检查
        """
        self.api_keys: List[ApiKeyConfig] = []
        self.strategy = load_balance_strategy
        self.health_check_interval = health_check_interval
        self.health_check_timeout = health_check_timeout
        self.enable_health_check = enable_health_check
        
        # 线程安全锁
        self._lock = threading.RLock()
        self._round_robin_index = 0
        
        # 健康检查线程
        self._health_check_thread = None
        self._stop_health_check = False
        
        if self.enable_health_check:
            self._start_health_check()

    def add_api_key(self, api_key: str, base_url: str, weight: int = 1, 
                   max_concurrent: int = 100, timeout: float = 30.0,
                   tpm: int = 1000000, rpm: int = 1000) -> None:
        """
        添加API密钥配置
        
        Args:
            api_key: API密钥
            base_url: 基础URL
            weight: 权重（用于权重负载均衡）
            max_concurrent: 最大并发数
            timeout: 请求超时时间
            tpm: 每分钟最大令牌数
            rpm: 每分钟最大请求数
        """
        with self._lock:
            config = ApiKeyConfig(
                api_key=api_key,
                base_url=base_url,
                weight=weight,
                max_concurrent=max_concurrent,
                timeout=timeout,
                tpm=tpm,
                rpm=rpm
            )
            self.api_keys.append(config)
            logger.info(f"添加API密钥配置: {base_url} (TPM: {tpm}, RPM: {rpm})")

    def add_api_keys_batch(self, api_keys_config: List[Dict[str, Any]]) -> None:
        """
        批量添加API密钥配置
        
        Args:
            api_keys_config: API密钥配置列表，每个配置包含：
                - api_key: API密钥 (必需)
                - base_url: 基础URL (必需)
                - weight: 权重 (可选，默认1)
                - max_concurrent: 最大并发数 (可选，默认100)
                - timeout: 超时时间 (可选，默认30.0)
                - tpm: 每分钟令牌数 (可选，默认1000000)
                - rpm: 每分钟请求数 (可选，默认1000)
                
        Usage:
            configs = [
                {"api_key": "sk-key1", "base_url": "https://api1.com"},
                {"api_key": "sk-key2", "base_url": "https://api2.com", "weight": 2},
                {"api_key": "sk-key3", "base_url": "https://api3.com", "tpm": 500000}
            ]
            manager.add_api_keys_batch(configs)
        """
        added_count = 0
        for config in api_keys_config:
            try:
                api_key = config.get('api_key')
                base_url = config.get('base_url')
                
                if not api_key or not base_url:
                    logger.error(f"跳过无效配置：缺少api_key或base_url - {config}")
                    continue
                
                self.add_api_key(
                    api_key=api_key,
                    base_url=base_url,
                    weight=config.get('weight', 1),
                    max_concurrent=config.get('max_concurrent', 100),
                    timeout=config.get('timeout', 30.0),
                    tpm=config.get('tpm', 1000000),
                    rpm=config.get('rpm', 1000)
                )
                added_count += 1
            except Exception as e:
                logger.error(f"添加API密钥配置失败: {config} - {e}")
        
        logger.info(f"批量添加完成：成功添加 {added_count}/{len(api_keys_config)} 个API密钥")

    def remove_api_key(self, api_key: str) -> bool:
        """
        移除API密钥配置
        
        Args:
            api_key: 要移除的API密钥
            
        Returns:
            bool: 是否成功移除
        """
        with self._lock:
            for i, config in enumerate(self.api_keys):
                if config.api_key == api_key:
                    del self.api_keys[i]
                    logger.info(f"移除API密钥: {api_key}")
                    return True
            return False

    def get_next_api_key(self, estimated_tokens: int = 0) -> Optional[ApiKeyConfig]:
        """
        根据负载均衡策略获取下一个可用的API密钥配置
        
        Args:
            estimated_tokens: 预估的令牌使用数量
            
        Returns:
            ApiKeyConfig: 选中的API密钥配置，如果没有可用的则返回None
        """
        with self._lock:
            # 筛选健康且未达到速率限制的密钥
            available_keys = [
                key for key in self.api_keys 
                if key.is_healthy and key.can_handle_request(estimated_tokens)
            ]
            
            if not available_keys:
                logger.warning("没有可用的API密钥（健康且未达到速率限制）")
                return None
            
            if self.strategy == LoadBalanceStrategy.ROUND_ROBIN:
                return self._round_robin_select(available_keys)
            elif self.strategy == LoadBalanceStrategy.WEIGHTED:
                return self._weighted_select(available_keys)
            elif self.strategy == LoadBalanceStrategy.LEAST_CONNECTIONS:
                return self._least_connections_select(available_keys)
            elif self.strategy == LoadBalanceStrategy.RANDOM:
                return self._random_select(available_keys)
            elif self.strategy == LoadBalanceStrategy.HEALTH_FIRST:
                return self._health_first_select(available_keys)
            else:
                return self._round_robin_select(available_keys)

    def _round_robin_select(self, available_keys: List[ApiKeyConfig]) -> ApiKeyConfig:
        """轮询选择"""
        if not available_keys:
            return None
        
        # 进一步过滤未达到最大并发的密钥
        final_keys = [key for key in available_keys if key.current_connections < key.max_concurrent]
        if not final_keys:
            final_keys = available_keys  # 如果都达到最大并发，则使用所有可用密钥
        
        with self._lock:
            selected = final_keys[self._round_robin_index % len(final_keys)]
            self._round_robin_index = (self._round_robin_index + 1) % len(self.api_keys)
        return selected

    def _weighted_select(self, available_keys: List[ApiKeyConfig]) -> ApiKeyConfig:
        """权重选择"""
        if not available_keys:
            return None
        
        # 进一步过滤未达到最大并发的密钥
        final_keys = [key for key in available_keys if key.current_connections < key.max_concurrent]
        if not final_keys:
            final_keys = available_keys
        
        # 计算权重总和
        total_weight = sum(key.weight for key in final_keys)
        if total_weight == 0:
            return final_keys[0]
        
        # 权重随机选择
        rand_weight = random.uniform(0, total_weight)
        current_weight = 0
        
        for key in final_keys:
            current_weight += key.weight
            if rand_weight <= current_weight:
                return key
        
        return final_keys[-1]

    def _least_connections_select(self, available_keys: List[ApiKeyConfig]) -> ApiKeyConfig:
        """最少连接选择"""
        if not available_keys:
            return None
        
        # 进一步过滤未达到最大并发的密钥
        final_keys = [key for key in available_keys if key.current_connections < key.max_concurrent]
        if not final_keys:
            final_keys = available_keys
        
        return min(final_keys, key=lambda x: x.current_connections)

    def _random_select(self, available_keys: List[ApiKeyConfig]) -> ApiKeyConfig:
        """随机选择"""
        if not available_keys:
            return None
        
        # 进一步过滤未达到最大并发的密钥
        final_keys = [key for key in available_keys if key.current_connections < key.max_concurrent]
        if not final_keys:
            final_keys = available_keys
        
        return random.choice(final_keys)

    def _health_first_select(self, available_keys: List[ApiKeyConfig]) -> ApiKeyConfig:
        """健康优先选择（根据成功率、响应时间和速率限制利用率）"""
        if not available_keys:
            return None
        
        # 进一步过滤未达到最大并发的密钥
        final_keys = [key for key in available_keys if key.current_connections < key.max_concurrent]
        if not final_keys:
            final_keys = available_keys
        
        # 计算健康分数（成功率 * 0.4 + (1 - 标准化响应时间) * 0.3 + (1 - 速率限制利用率) * 0.3）
        def health_score(key: ApiKeyConfig) -> float:
            success_rate = key.success_rate
            avg_time = key.avg_response_time
            max_time = max([k.avg_response_time for k in final_keys] + [1.0])
            normalized_time = avg_time / max_time if max_time > 0 else 0
            
            # 考虑速率限制利用率
            rate_utilization = max(key.rpm_utilization, key.tpm_utilization)
            
            return success_rate * 0.4 + (1 - normalized_time) * 0.3 + (1 - rate_utilization) * 0.3
        
        return max(final_keys, key=health_score)

    def acquire_api_key(self, estimated_tokens: int = 0) -> Optional[Tuple[str, str, float]]:
        """
        获取API密钥用于请求
        
        Args:
            estimated_tokens: 预估的令牌使用数量
            
        Returns:
            Tuple[str, str, float]: (api_key, base_url, timeout) 或 None
        """
        config = self.get_next_api_key(estimated_tokens)
        if config is None:
            return None
        
        with self._lock:
            config.current_connections += 1
            config.record_request(0)  # 先记录请求，令牌数稍后在release时更新
        
        return config.api_key, config.base_url, config.timeout

    def get_api_key(self, estimated_tokens: int = 0) -> Tuple[str, str]:
        """
        用户友好的API密钥获取接口 - 总是返回一个API密钥
        
        Args:
            estimated_tokens: 预估的令牌使用数量
            
        Returns:
            (api_key, base_url) 元组，保证返回一个可用的API密钥
            
        Usage:
            api_key, base_url = manager.get_api_key()
            # 总是会返回一个API密钥，无需判断
            # 记得调用 manager.mark_request_complete(api_key, success, tokens_used)
        """
        # 首先尝试获取完全可用的API密钥
        result = self.acquire_api_key(estimated_tokens)
        if result is not None:
            return result[0], result[1]
        
        # 如果所有密钥都不可用，返回最快可用的（速率限制恢复最快的）
        return self._get_fastest_available_key()

    def _get_fastest_available_key(self) -> Tuple[str, str]:
        """获取最快可用的API密钥（当所有密钥都暂时不可用时）"""
        with self._lock:
            if not self.api_keys:
                raise ValueError("没有配置任何API密钥")
            
            # 计算每个密钥恢复可用的时间
            current_time = time.time()
            best_key = None
            min_wait_time = float('inf')
            
            for config in self.api_keys:
                if not config.is_healthy:
                    continue  # 跳过不健康的密钥
                
                # 计算RPM恢复时间
                rpm_wait_time = 0
                if config.current_rpm_usage >= config.rpm:
                    # 找到最早的请求时间戳，计算恢复时间
                    if config.request_timestamps:
                        earliest_request = min(config.request_timestamps)
                        rpm_wait_time = max(0, 60 - (current_time - earliest_request))
                
                # 计算TPM恢复时间
                tpm_wait_time = 0
                if config.current_tpm_usage >= config.tpm:
                    if config.token_usage_timestamps:
                        earliest_token_usage = min(ts for ts, _ in config.token_usage_timestamps)
                        tpm_wait_time = max(0, 60 - (current_time - earliest_token_usage))
                
                # 取最大等待时间
                total_wait_time = max(rpm_wait_time, tpm_wait_time)
                
                if total_wait_time < min_wait_time:
                    min_wait_time = total_wait_time
                    best_key = config
            
            # 如果没有健康的密钥，选择第一个
            if best_key is None:
                best_key = self.api_keys[0]
                logger.warning(f"所有API密钥都不健康，返回第一个密钥: {best_key.base_url}")
            else:
                if min_wait_time > 0:
                    logger.warning(f"所有API密钥都达到速率限制，返回最快恢复的密钥: {best_key.base_url} (预计{min_wait_time:.1f}秒后恢复)")
            
            # 强制获取该密钥（忽略速率限制）
            best_key.current_connections += 1
            best_key.total_requests += 1
            best_key.record_request(0)
            
            return best_key.api_key, best_key.base_url

    def mark_request_complete(self, api_key: str, success: bool = True, 
                            response_time: float = 0.0, tokens_used: int = 0) -> None:
        """
        标记请求完成（用于配合get_api_key使用）
        
        Args:
            api_key: API密钥
            success: 请求是否成功
            response_time: 响应时间
            tokens_used: 实际使用的令牌数
        """
        self.release_api_key(api_key, success, response_time, tokens_used)

    def get_api_keys(self, count: int = 1, estimated_tokens: int = 0) -> List[Tuple[str, str]]:
        """
        获取多个API密钥 - 总是返回请求数量的API密钥
        
        Args:
            count: 需要获取的API密钥数量
            estimated_tokens: 每个请求预估的令牌使用数量
            
        Returns:
            List[Tuple[str, str]]: [(api_key, base_url), ...] 列表，保证返回count个密钥
            
        Usage:
            api_keys = manager.get_api_keys(count=3)
            for api_key, base_url in api_keys:
                # 使用api_key和base_url进行API调用
                # 记得调用 manager.mark_request_complete(api_key, success, tokens_used)
                pass
        """
        if count <= 0:
            return []
        
        if not self.api_keys:
            raise ValueError("没有配置任何API密钥")
        
        results = []
        available_keys = set()  # 记录已分配的密钥，避免重复
        
        for i in range(count):
            # 先尝试获取不同的API密钥
            attempts = 0
            while attempts < len(self.api_keys) * 2:  # 最多尝试2轮
                api_key, base_url = self.get_api_key(estimated_tokens)
                key_id = f"{api_key}_{base_url}"
                
                if key_id not in available_keys or len(available_keys) >= len(self.api_keys):
                    # 如果是新密钥或者所有密钥都已用过，则接受
                    available_keys.add(key_id)
                    results.append((api_key, base_url))
                    break
                
                attempts += 1
            else:
                # 如果无法获取不同的密钥，直接获取任意一个
                api_key, base_url = self.get_api_key(estimated_tokens)
                results.append((api_key, base_url))
        
        return results

    def get_all_available_api_keys(self, estimated_tokens: int = 0) -> List[Tuple[str, str]]:
        """
        获取所有可用的API密钥
        
        Args:
            estimated_tokens: 每个请求预估的令牌使用数量
            
        Returns:
            List[Tuple[str, str]]: [(api_key, base_url), ...] 列表
            
        Usage:
            api_keys = manager.get_all_available_api_keys()
            print(f"当前有 {len(api_keys)} 个可用的API密钥")
        """
        results = []
        with self._lock:
            for config in self.api_keys:
                if (config.is_healthy and 
                    config.current_connections < config.max_concurrent and
                    config.can_handle_request(estimated_tokens)):
                    results.append((config.api_key, config.base_url))
        return results

    def release_api_key(self, api_key: str, success: bool = True, response_time: float = 0.0, 
                       tokens_used: int = 0) -> None:
        """
        释放API密钥并更新统计信息
        
        Args:
            api_key: API密钥
            success: 请求是否成功
            response_time: 响应时间
            tokens_used: 实际使用的令牌数
        """
        with self._lock:
            for config in self.api_keys:
                if config.api_key == api_key:
                    config.current_connections = max(0, config.current_connections - 1)
                    config.total_requests += 1
                    
                    if not success:
                        config.failed_requests += 1
                    
                    if response_time > 0:
                        config.response_times.append(response_time)
                        # 只保留最近100次的响应时间
                        if len(config.response_times) > 100:
                            config.response_times = config.response_times[-100:]
                    
                    # 更新令牌使用记录
                    if tokens_used > 0:
                        # 移除最后一次的记录（在acquire时添加的0令牌记录）
                        if config.token_usage_timestamps and config.token_usage_timestamps[-1][1] == 0:
                            config.token_usage_timestamps[-1] = (config.token_usage_timestamps[-1][0], tokens_used)
                        else:
                            config.token_usage_timestamps.append((time.time(), tokens_used))
                    
                    break

    def _start_health_check(self) -> None:
        """启动健康检查线程"""
        self._health_check_thread = threading.Thread(target=self._health_check_worker, daemon=True)
        self._health_check_thread.start()
        logger.info("健康检查线程已启动")

    def _health_check_worker(self) -> None:
        """健康检查工作线程"""
        while not self._stop_health_check:
            try:
                self._perform_health_check()
                time.sleep(self.health_check_interval)
            except Exception as e:
                logger.error(f"健康检查出错: {e}")
                time.sleep(5)

    def _perform_health_check(self) -> None:
        """执行健康检查"""
        with self._lock:
            for config in self.api_keys:
                try:
                    # 发送健康检查请求
                    start_time = time.time()
                    response = requests.get(
                        f"{config.base_url.rstrip('/')}/health",  # 假设有health端点
                        timeout=self.health_check_timeout,
                        headers={"Authorization": f"Bearer {config.api_key}"}
                    )
                    response_time = time.time() - start_time
                    
                    # 更新健康状态
                    was_healthy = config.is_healthy
                    config.is_healthy = response.status_code == 200
                    config.last_health_check = time.time()
                    
                    if config.is_healthy:
                        config.response_times.append(response_time)
                        if len(config.response_times) > 100:
                            config.response_times = config.response_times[-100:]
                    
                    if was_healthy != config.is_healthy:
                        status = "健康" if config.is_healthy else "不健康"
                        logger.info(f"API密钥状态变更: {config.base_url} -> {status}")
                
                except Exception as e:
                    was_healthy = config.is_healthy
                    config.is_healthy = False
                    config.last_health_check = time.time()
                    
                    if was_healthy:
                        logger.warning(f"API密钥健康检查失败: {config.base_url} - {e}")

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            Dict: 包含各种统计信息的字典
        """
        with self._lock:
            stats = {
                "total_keys": len(self.api_keys),
                "healthy_keys": len([k for k in self.api_keys if k.is_healthy]),
                "total_connections": sum(k.current_connections for k in self.api_keys),
                "strategy": self.strategy.value,
                "keys_details": []
            }
            
            for config in self.api_keys:
                key_stats = {
                    "base_url": config.base_url,
                    "is_healthy": config.is_healthy,
                    "current_connections": config.current_connections,
                    "total_requests": config.total_requests,
                    "failed_requests": config.failed_requests,
                    "success_rate": config.success_rate,
                    "avg_response_time": config.avg_response_time,
                    "weight": config.weight,
                    "max_concurrent": config.max_concurrent,
                    "tpm": config.tpm,
                    "rpm": config.rpm,
                    "current_rpm_usage": config.current_rpm_usage,
                    "current_tpm_usage": config.current_tpm_usage,
                    "rpm_utilization": config.rpm_utilization,
                    "tpm_utilization": config.tpm_utilization
                }
                stats["keys_details"].append(key_stats)
            
            return stats

    def close(self) -> None:
        """关闭管理器并清理资源"""
        self._stop_health_check = True
        if self._health_check_thread and self._health_check_thread.is_alive():
            self._health_check_thread.join(timeout=5)
        logger.info("API密钥管理器已关闭")


class ApiKeyContextManager:
    """API密钥上下文管理器，用于自动获取和释放API密钥"""
    
    def __init__(self, manager: ApiKeyManager, estimated_tokens: int = 0):
        self.manager = manager
        self.estimated_tokens = estimated_tokens
        self.api_key = None
        self.base_url = None
        self.timeout = None
        self.start_time = None

    def __enter__(self) -> Tuple[Optional[str], Optional[str], Optional[float]]:
        """进入上下文时获取API密钥"""
        result = self.manager.acquire_api_key(self.estimated_tokens)
        if result:
            self.api_key, self.base_url, self.timeout = result
            self.start_time = time.time()
        return result

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文时释放API密钥"""
        if self.api_key:
            success = exc_type is None
            response_time = time.time() - self.start_time if self.start_time else 0
            # 这里可以根据实际情况传入真实的tokens_used
            self.manager.release_api_key(self.api_key, success, response_time, tokens_used=0)


# 使用示例和工具函数
def create_default_manager() -> ApiKeyManager:
    """创建默认配置的API密钥管理器"""
    return ApiKeyManager(
        load_balance_strategy=LoadBalanceStrategy.LEAST_CONNECTIONS,
        health_check_interval=30.0,
        enable_health_check=True
    )


def demo_usage():
    """演示用法"""
    print("=== API密钥管理器演示 ===")
    
    # 创建管理器
    manager = create_default_manager()
    
    # 添加多个API密钥，现在默认TPM=100万，RPM=1000
    manager.add_api_key("sk-openai-key-1", "https://api.openai.com", weight=2, max_concurrent=50)
    manager.add_api_key("sk-claude-key-1", "https://api.anthropic.com", weight=1, max_concurrent=30)
    manager.add_api_key("sk-deepseek-key-1", "https://api.deepseek.com", weight=3, max_concurrent=80)
    manager.add_api_key("sk-gemini-key-1", "https://generativelanguage.googleapis.com", weight=2, max_concurrent=60)
    
    print(f"已添加 {len(manager.api_keys)} 个API密钥")
    
    # 方式1: 获取单个API密钥（推荐用法）
    print("\n=== 方式1: 获取单个API密钥 ===")
    api_key, base_url = manager.get_api_key(estimated_tokens=1000)
    if api_key:
        print(f"获取到API密钥: {api_key[:15]}...")
        print(f"Base URL: {base_url}")
        
        # 模拟API调用
        import time
        start_time = time.time()
        time.sleep(0.1)  # 模拟API调用延迟
        response_time = time.time() - start_time
        
        # 标记请求完成
        manager.mark_request_complete(api_key, success=True, response_time=response_time, tokens_used=1000)
        print("请求完成并已标记")
    else:
        print("没有可用的API密钥")
    
    # 方式2: 获取多个API密钥
    print("\n=== 方式2: 获取多个API密钥 ===")
    api_keys = manager.get_api_keys(count=3, estimated_tokens=500)
    print(f"获取到 {len(api_keys)} 个API密钥:")
    for i, (api_key, base_url) in enumerate(api_keys, 1):
        print(f"  {i}. {api_key[:15]}... -> {base_url}")
        # 模拟使用后标记完成
        manager.mark_request_complete(api_key, success=True, tokens_used=500)
    
    # 方式3: 获取所有可用的API密钥
    print("\n=== 方式3: 获取所有可用API密钥 ===")
    all_keys = manager.get_all_available_api_keys()
    print(f"当前有 {len(all_keys)} 个可用的API密钥:")
    for i, (api_key, base_url) in enumerate(all_keys, 1):
        print(f"  {i}. {api_key[:15]}... -> {base_url}")
    
    # 方式4: 使用上下文管理器（自动管理）
    print("\n=== 方式4: 使用上下文管理器 ===")
    with ApiKeyContextManager(manager, estimated_tokens=1000) as (api_key, base_url, timeout):
        if api_key:
            print(f"上下文中获取API密钥: {api_key[:15]}...")
            print(f"Base URL: {base_url}, Timeout: {timeout}s")
            # 在这里进行实际的API请求
            time.sleep(0.05)  # 模拟API调用
        else:
            print("上下文中没有可用的API密钥")
    
    # 查看统计信息
    print("\n=== 统计信息 ===")
    stats = manager.get_statistics()
    print(f"总密钥数: {stats['total_keys']}")
    print(f"健康密钥数: {stats['healthy_keys']}")
    print(f"当前连接数: {stats['total_connections']}")
    print(f"负载均衡策略: {stats['strategy']}")
    
    print("\n各密钥详细信息:")
    for key_stat in stats['keys_details']:
        print(f"  URL: {key_stat['base_url']}")
        print(f"    健康状态: {key_stat['is_healthy']}")
        print(f"    总请求数: {key_stat['total_requests']}")
        print(f"    成功率: {key_stat['success_rate']:.2%}")
        print(f"    RPM使用: {key_stat['current_rpm_usage']}/{key_stat['rpm']} ({key_stat['rpm_utilization']:.1%})")
        print(f"    TPM使用: {key_stat['current_tpm_usage']}/{key_stat['tpm']} ({key_stat['tpm_utilization']:.1%})")
        print()
    
    # 关闭管理器
    manager.close()
    print("管理器已关闭")


if __name__ == "__main__":
    demo_usage() 