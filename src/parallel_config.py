#!/usr/bin/env python3
"""
parallel_config.py - Configuration for staggered parallel task execution
"""
import os
from dataclasses import dataclass


@dataclass
class ParallelConfig:
    """Configuration for staggered parallel task execution"""
    
    # Enable/disable parallel execution globally
    enabled: bool = True
    
    # Maximum number of parallel workers (high limit for staggered execution)
    # This is now a soft limit - actual parallelism controlled by staggering
    max_workers: int = 100
    
    # Maximum concurrent LLM API calls (CRITICAL for preventing 429 errors)
    # This is the PRIMARY bottleneck for parallel execution
    max_llm_concurrent: int = 5
    
    # Retry settings for LLM calls
    llm_max_retries: int = 5
    llm_base_delay: int = 2  # seconds
    
    # Docker settings
    docker_timeout: int = 90  # seconds
    
    # File naming to avoid collisions
    use_node_prefixes: bool = True
    
    # Staggered execution settings
    stagger_batch_size: int = 2      # Start N nodes per batch
    stagger_delay: int = 180          # Wait N seconds between batches (3 minutes)
    
    @classmethod
    def from_env(cls) -> 'ParallelConfig':
        """Create configuration from environment variables"""
        return cls(
            enabled=os.getenv('PARALLEL_ENABLED', 'true').lower() == 'true',
            max_workers=int(os.getenv('PARALLEL_MAX_WORKERS', '100')),
            max_llm_concurrent=int(os.getenv('PARALLEL_MAX_LLM', '5')),
            llm_max_retries=int(os.getenv('LLM_MAX_RETRIES', '5')),
            llm_base_delay=int(os.getenv('LLM_BASE_DELAY', '2')),
            docker_timeout=int(os.getenv('DOCKER_TIMEOUT', '90')),
            use_node_prefixes=os.getenv('USE_NODE_PREFIXES', 'true').lower() == 'true',
            stagger_batch_size=int(os.getenv('STAGGER_BATCH_SIZE', '2')),
            stagger_delay=int(os.getenv('STAGGER_DELAY', '180'))
        )
    
    def apply_to_task_node(self):
        """Apply this configuration to TaskNode class"""
        from src.task_node import TaskNode
        from threading import Semaphore
        from concurrent.futures import ThreadPoolExecutor
        
        # Update class-level resources
        TaskNode._llm_semaphore = Semaphore(self.max_llm_concurrent)
        TaskNode._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="TaskNode"
        )
        TaskNode.STAGGER_BATCH_SIZE = self.stagger_batch_size
        TaskNode.STAGGER_DELAY = self.stagger_delay
        
        print(f"[ParallelConfig] Applied to TaskNode:")
        print(f"  - ThreadPoolExecutor: {self.max_workers} workers")
        print(f"  - LLM Semaphore: {self.max_llm_concurrent} concurrent")
        print(f"  - Stagger: {self.stagger_batch_size} nodes every {self.stagger_delay}s")
    
    def __str__(self) -> str:
        """String representation for logging"""
        return (
            f"ParallelConfig(\n"
            f"  enabled={self.enabled}\n"
            f"  max_workers={self.max_workers} (soft limit)\n"
            f"  max_llm_concurrent={self.max_llm_concurrent} (CRITICAL)\n"
            f"  llm_max_retries={self.llm_max_retries}\n"
            f"  llm_base_delay={self.llm_base_delay}s\n"
            f"  docker_timeout={self.docker_timeout}s\n"
            f"  use_node_prefixes={self.use_node_prefixes}\n"
            f"  stagger_batch_size={self.stagger_batch_size}\n"
            f"  stagger_delay={self.stagger_delay}s (3 min)\n"
            f")"
        )


# Global configuration instance
_config = None


def get_config() -> ParallelConfig:
    """Get or create global configuration"""
    global _config
    if _config is None:
        _config = ParallelConfig.from_env()
    return _config


def set_config(config: ParallelConfig):
    """Set global configuration"""
    global _config
    _config = config
    config.apply_to_task_node()


def init_parallel_config():
    """Initialize parallel configuration from environment"""
    config = ParallelConfig.from_env()
    set_config(config)
    return config