#!/usr/bin/env python3
"""
execution_limits.py - Configurable execution limits and kill-switches
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionLimits:
    """
    Soft kill-switches for task execution.
    
    These limits prevent infinite loops and runaway tasks without
    hard-killing processes (which could corrupt Docker state).
    """
    
    # MCP Agent limits
    mcp_max_iterations: int = 20           # Max conversation turns
    mcp_empty_output_threshold: int = 5    # Consecutive empty outputs before warning
    mcp_comment_only_threshold: int = 5    # Consecutive comments before force-stop
    mcp_command_timeout: int = 300         # Max seconds per Docker command (5 min)
    
    # LLM API limits
    llm_max_retries: int = 5               # Max retry attempts per LLM call
    llm_base_delay: int = 2                # Base delay for exponential backoff (seconds)
    llm_call_timeout: int = 90             # Max seconds per LLM API call
    
    # Task Node limits
    task_direct_retries: int = 3           # Max attempts for direct_execute()
    task_max_replans: int = 2              # Max branching replans before giving up
    task_llm_failure_threshold: int = 3    # LLM failures before circuit breaker
    
    # Cancellation check frequency
    cancellation_check_interval: int = 5   # Check every N seconds during long operations
    
    # Concurrency limits (delegated to ThreadPoolExecutor)
    max_concurrent_tasks: int = 10         # Max parallel task executions
    executor_queue_size: Optional[int] = None  # None = unlimited queue
    
    # Docker command safety
    docker_exec_timeout: int = 300         # Max seconds for docker.exec_run()
    docker_kill_on_timeout: bool = False   # Kill container on timeout (dangerous!)
    
    # Monitoring
    enable_metrics: bool = True            # Track execution statistics
    log_slow_commands: bool = True         # Log commands exceeding 50% of timeout
    
    @classmethod
    def from_env(cls) -> 'ExecutionLimits':
        """Create configuration from environment variables"""
        return cls(
            mcp_max_iterations=int(os.getenv('MCP_MAX_ITERATIONS', '20')),
            mcp_empty_output_threshold=int(os.getenv('MCP_EMPTY_THRESHOLD', '5')),
            mcp_comment_only_threshold=int(os.getenv('MCP_COMMENT_THRESHOLD', '5')),
            mcp_command_timeout=int(os.getenv('MCP_COMMAND_TIMEOUT', '300')),
            
            llm_max_retries=int(os.getenv('LLM_MAX_RETRIES', '5')),
            llm_base_delay=int(os.getenv('LLM_BASE_DELAY', '2')),
            llm_call_timeout=int(os.getenv('LLM_CALL_TIMEOUT', '90')),
            
            task_direct_retries=int(os.getenv('TASK_DIRECT_RETRIES', '3')),
            task_max_replans=int(os.getenv('TASK_MAX_REPLANS', '2')),
            task_llm_failure_threshold=int(os.getenv('TASK_LLM_FAILURE_THRESHOLD', '3')),
            
            cancellation_check_interval=int(os.getenv('CANCEL_CHECK_INTERVAL', '5')),
            
            max_concurrent_tasks=int(os.getenv('MAX_CONCURRENT_TASKS', '10')),
            executor_queue_size=int(os.getenv('EXECUTOR_QUEUE_SIZE', '0')) or None,
            
            docker_exec_timeout=int(os.getenv('DOCKER_EXEC_TIMEOUT', '300')),
            docker_kill_on_timeout=os.getenv('DOCKER_KILL_ON_TIMEOUT', 'false').lower() == 'true',
            
            enable_metrics=os.getenv('ENABLE_METRICS', 'true').lower() == 'true',
            log_slow_commands=os.getenv('LOG_SLOW_COMMANDS', 'true').lower() == 'true'
        )
    
    def to_dict(self) -> dict:
        """Export as dictionary for API responses"""
        return {
            'mcp': {
                'max_iterations': self.mcp_max_iterations,
                'empty_output_threshold': self.mcp_empty_output_threshold,
                'comment_only_threshold': self.mcp_comment_only_threshold,
                'command_timeout': self.mcp_command_timeout
            },
            'llm': {
                'max_retries': self.llm_max_retries,
                'base_delay': self.llm_base_delay,
                'call_timeout': self.llm_call_timeout
            },
            'task': {
                'direct_retries': self.task_direct_retries,
                'max_replans': self.task_max_replans,
                'llm_failure_threshold': self.task_llm_failure_threshold
            },
            'cancellation': {
                'check_interval': self.cancellation_check_interval
            },
            'concurrency': {
                'max_concurrent_tasks': self.max_concurrent_tasks,
                'executor_queue_size': self.executor_queue_size
            },
            'docker': {
                'exec_timeout': self.docker_exec_timeout,
                'kill_on_timeout': self.docker_kill_on_timeout
            },
            'monitoring': {
                'enable_metrics': self.enable_metrics,
                'log_slow_commands': self.log_slow_commands
            }
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ExecutionLimits':
        """Create from dictionary (for API updates)"""
        return cls(
            mcp_max_iterations=data.get('mcp', {}).get('max_iterations', 20),
            mcp_empty_output_threshold=data.get('mcp', {}).get('empty_output_threshold', 5),
            mcp_comment_only_threshold=data.get('mcp', {}).get('comment_only_threshold', 5),
            mcp_command_timeout=data.get('mcp', {}).get('command_timeout', 300),
            
            llm_max_retries=data.get('llm', {}).get('max_retries', 5),
            llm_base_delay=data.get('llm', {}).get('base_delay', 2),
            llm_call_timeout=data.get('llm', {}).get('call_timeout', 90),
            
            task_direct_retries=data.get('task', {}).get('direct_retries', 3),
            task_max_replans=data.get('task', {}).get('max_replans', 2),
            task_llm_failure_threshold=data.get('task', {}).get('llm_failure_threshold', 3),
            
            cancellation_check_interval=data.get('cancellation', {}).get('check_interval', 5),
            
            max_concurrent_tasks=data.get('concurrency', {}).get('max_concurrent_tasks', 10),
            executor_queue_size=data.get('concurrency', {}).get('executor_queue_size'),
            
            docker_exec_timeout=data.get('docker', {}).get('exec_timeout', 300),
            docker_kill_on_timeout=data.get('docker', {}).get('kill_on_timeout', False),
            
            enable_metrics=data.get('monitoring', {}).get('enable_metrics', True),
            log_slow_commands=data.get('monitoring', {}).get('log_slow_commands', True)
        )
    
    def __str__(self) -> str:
        """Human-readable summary"""
        return f"""ExecutionLimits(
  MCP: {self.mcp_max_iterations} iterations, {self.mcp_command_timeout}s command timeout
  LLM: {self.llm_max_retries} retries, {self.llm_call_timeout}s call timeout
  Task: {self.task_direct_retries} retries, {self.task_max_replans} replans
  Concurrency: {self.max_concurrent_tasks} workers
  Docker: {self.docker_exec_timeout}s exec timeout
)"""


class ExecutionMetrics:
    """
    Tracks execution statistics for monitoring.
    Thread-safe counter for kill-switch hits.
    """
    
    def __init__(self):
        from threading import Lock
        self.lock = Lock()
        self.reset()
    
    def reset(self):
        """Reset all counters"""
        with self.lock:
            self.mcp_timeouts = 0
            self.mcp_iteration_limits = 0
            self.mcp_comment_loops = 0
            
            self.llm_rate_limits = 0
            self.llm_failures = 0
            self.llm_circuit_breaks = 0
            
            self.task_retries_exhausted = 0
            self.task_impossible = 0
            
            self.cancellations = 0
            
            self.docker_timeouts = 0
            self.docker_slow_commands = 0
    
    def increment(self, metric: str):
        """Thread-safe increment"""
        with self.lock:
            if hasattr(self, metric):
                setattr(self, metric, getattr(self, metric) + 1)
    
    def to_dict(self) -> dict:
        """Export metrics"""
        with self.lock:
            return {
                'mcp': {
                    'timeouts': self.mcp_timeouts,
                    'iteration_limits': self.mcp_iteration_limits,
                    'comment_loops': self.mcp_comment_loops
                },
                'llm': {
                    'rate_limits': self.llm_rate_limits,
                    'failures': self.llm_failures,
                    'circuit_breaks': self.llm_circuit_breaks
                },
                'task': {
                    'retries_exhausted': self.task_retries_exhausted,
                    'impossible': self.task_impossible
                },
                'cancellations': self.cancellations,
                'docker': {
                    'timeouts': self.docker_timeouts,
                    'slow_commands': self.docker_slow_commands
                }
            }


# Global instances
_limits = None
_metrics = ExecutionMetrics()


def get_limits() -> ExecutionLimits:
    """Get or create global limits configuration"""
    global _limits
    if _limits is None:
        _limits = ExecutionLimits.from_env()
    return _limits


def set_limits(limits: ExecutionLimits):
    """Set global limits configuration"""
    global _limits
    _limits = limits


def get_metrics() -> ExecutionMetrics:
    """Get global metrics tracker"""
    return _metrics


def init_execution_limits():
    """Initialize execution limits from environment"""
    limits = ExecutionLimits.from_env()
    set_limits(limits)
    return limits