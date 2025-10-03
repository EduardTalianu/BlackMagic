#!/usr/bin/env python3
"""
task_models.py - Pydantic models for task management
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from enum import Enum
from datetime import datetime


class TaskStatus(str, Enum):
    """Task execution status"""
    PENDING = "pending"
    PLANNING = "planning"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    IMPOSSIBLE = "impossible"


class TaskModel(BaseModel):
    """Base task model with abstract, description, and verification"""
    model_config = ConfigDict(extra='allow')  # Pydantic v2 syntax
    
    abstract: str = Field(..., description="Brief one-line summary of the task")
    description: str = Field(..., description="Detailed step-by-step description")
    verification: str = Field(..., description="Criteria to verify task completion")
    task_status: TaskStatus = Field(default=TaskStatus.PENDING)
    node_id: Optional[str] = Field(default=None, description="Graph node identifier")
    parent_id: Optional[str] = Field(default=None, description="Parent node identifier")


class TaskResult(BaseModel):
    """Result of task execution"""
    success: bool
    output: str
    summary: str
    verification_met: bool = False


class SubTask(BaseModel):
    """Individual sub-task in a chain"""
    abstract: str
    description: str
    verification: str
    rationale: str = Field(description="Why this sub-task is needed")


class TaskChain(BaseModel):
    """Chain of sub-tasks from the planner"""
    tasks: List[SubTask]
    strategy: str = Field(description="Overall strategy for task decomposition")


class BranchRequirement(BaseModel):
    """Decision on whether to branch into sub-tasks"""
    needs_branching: bool
    task_chain: TaskChain
    reasoning: str


class TaskModelOut(BaseModel):
    """Output model for completed tasks"""
    task_id: str
    abstract: str
    description: str
    verification: str
    status: TaskStatus
    result: Optional[str] = None
    graph: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class TaskStatusResponse(BaseModel):
    """Response for task status queries"""
    task_id: str
    status: TaskStatus
    abstract: str
    description: str
    verification: str
    result: Optional[str] = None
    graph: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class TaskResult(BaseModel):
    """Result of task execution"""
    success: bool
    output: str
    summary: str
    verification_met: bool = False


class SubTask(BaseModel):
    """Individual sub-task in a chain"""
    abstract: str
    description: str
    verification: str
    rationale: str = Field(description="Why this sub-task is needed")


class TaskChain(BaseModel):
    """Chain of sub-tasks from the planner"""
    tasks: List[SubTask]
    strategy: str = Field(description="Overall strategy for task decomposition")


class BranchRequirement(BaseModel):
    """Decision on whether to branch into sub-tasks"""
    needs_branching: bool
    task_chain: TaskChain
    reasoning: str


class TaskModelOut(BaseModel):
    """Output model for completed tasks"""
    task_id: str
    abstract: str
    description: str
    verification: str
    status: TaskStatus
    result: Optional[str] = None
    graph: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class TaskStatusResponse(BaseModel):
    """Response for task status queries"""
    task_id: str
    status: TaskStatus
    abstract: str
    description: str
    verification: str
    result: Optional[str] = None
    graph: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None