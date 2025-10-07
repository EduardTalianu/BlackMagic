#!/usr/bin/env python3
"""
graph_directions.py - 4-direction topological graph for red team task trees
"""
from enum import Enum, auto
from typing import Dict, List, Optional, Set
from threading import Lock


class Direction(Enum):
    """Four-direction navigation for 2D task graphs"""
    UP = auto()      # Parent / container task
    DOWN = auto()    # Child / sub-task
    LEFT = auto()    # Previous sibling (back-reference)
    RIGHT = auto()   # Next sibling (sequential step)


def reverse_direction(d: Direction) -> Direction:
    """Flip arrow direction for bidirectional edges"""
    return {
        Direction.UP: Direction.DOWN,
        Direction.DOWN: Direction.UP,
        Direction.LEFT: Direction.RIGHT,
        Direction.RIGHT: Direction.LEFT
    }[d]


class DirectionalGraph:
    """
    2D task graph with explicit UP/DOWN/LEFT/RIGHT navigation.
    Automatically maintains bidirectional edges.
    """
    
    def __init__(self):
        # Core storage: node_id -> {Direction -> neighbor_id}
        self.relations: Dict[str, Dict[Direction, Optional[str]]] = {}
        
        # Node metadata (abstract, description, status)
        self.nodes: Dict[str, dict] = {}
        
        # Thread safety
        self.lock = Lock()
    
    def add_node(self, node_id: str, **metadata) -> None:
        """
        Initialize node with all directions as None.
        
        Args:
            node_id: Unique node identifier
            **metadata: Node properties (abstract, description, status, etc.)
        """
        with self.lock:
            # Initialize directional links
            self.relations[node_id] = {
                Direction.UP: None,
                Direction.DOWN: None,
                Direction.LEFT: None,
                Direction.RIGHT: None
            }
            
            # Store metadata
            self.nodes[node_id] = metadata
    
    def add_edge(
        self, 
        from_id: str, 
        direction: Direction, 
        to_id: Optional[str],
        overwrite: bool = False
    ) -> None:
        """
        Add bidirectional edge with automatic reverse linking.
        
        Args:
            from_id: Source node
            direction: Edge direction (UP/DOWN/LEFT/RIGHT)
            to_id: Target node (None to break edge)
            overwrite: If False, raises error if edge already exists
        
        Example:
            # Creates A →↓ B and B →↑ A automatically
            graph.add_edge("A", Direction.DOWN, "B")
        """
        with self.lock:
            # Ensure nodes exist
            if from_id not in self.relations:
                raise ValueError(f"Node {from_id} does not exist")
            if to_id and to_id not in self.relations:
                raise ValueError(f"Node {to_id} does not exist")
            
            # Check existing edge
            existing = self.relations[from_id][direction]
            if existing and not overwrite and to_id != existing:
                raise ValueError(
                    f"Edge {from_id} --{direction.name}--> {existing} already exists. "
                    f"Use overwrite=True to replace."
                )
            
            # Break old reverse edge if overwriting
            if existing and existing != to_id:
                reverse_dir = reverse_direction(direction)
                self.relations[existing][reverse_dir] = None
            
            # Add forward edge
            self.relations[from_id][direction] = to_id
            
            # Add reverse edge automatically
            if to_id:
                reverse_dir = reverse_direction(direction)
                self.relations[to_id][reverse_dir] = from_id
    
    def remove_edge(self, from_id: str, direction: Direction) -> None:
        """Remove edge and its reverse"""
        self.add_edge(from_id, direction, None, overwrite=True)
    
    def get_neighbor(self, node_id: str, direction: Direction) -> Optional[str]:
        """
        Unified API for all directional queries.
        
        Returns:
            Neighbor node ID or None
        
        Example:
            parent_id = graph.get_neighbor("B", Direction.UP)
        """
        with self.lock:
            return self.relations.get(node_id, {}).get(direction)
    
    def traverse_direction(
        self, 
        node_id: str, 
        direction: Direction,
        include_self: bool = False
    ) -> List[str]:
        """
        Traverse in one direction until hitting None.
        
        Args:
            node_id: Starting node
            direction: Direction to traverse (UP/DOWN/LEFT/RIGHT)
            include_self: Include starting node in result
        
        Returns:
            List of node IDs in traversal order
        
        Example:
            # Get all ancestors
            ancestors = graph.traverse_direction("F", Direction.UP)
            
            # Get all previous siblings
            prev_siblings = graph.traverse_direction("C", Direction.LEFT)
        """
        path = [node_id] if include_self else []
        current = node_id
        
        while current := self.get_neighbor(current, direction):
            path.append(current)
        
        return path
    
    def get_parent(self, node_id: str) -> Optional[str]:
        """Convenience method: get parent"""
        return self.get_neighbor(node_id, Direction.UP)
    
    def get_children(self, node_id: str) -> List[str]:
        """Get all children by traversing DOWN then RIGHT"""
        children = []
        first_child = self.get_neighbor(node_id, Direction.DOWN)
        
        if first_child:
            # Traverse sibling chain
            children = self.traverse_direction(first_child, Direction.RIGHT, include_self=True)
        
        return children
    
    def get_siblings(self, node_id: str, include_self: bool = False) -> List[str]:
        """Get all siblings (nodes with same parent)"""
        parent_id = self.get_parent(node_id)
        if not parent_id:
            return [node_id] if include_self else []
        
        siblings = self.get_children(parent_id)
        
        if not include_self:
            siblings = [s for s in siblings if s != node_id]
        
        return siblings
    
    def get_prev_siblings(self, node_id: str) -> List[str]:
        """Get all previous siblings (LEFT chain)"""
        return self.traverse_direction(node_id, Direction.LEFT)
    
    def get_next_siblings(self, node_id: str) -> List[str]:
        """Get all next siblings (RIGHT chain)"""
        return self.traverse_direction(node_id, Direction.RIGHT)
    
    def get_ancestors(self, node_id: str) -> List[str]:
        """Get all ancestors (UP chain)"""
        return self.traverse_direction(node_id, Direction.UP)
    
    def get_descendants(self, node_id: str) -> Set[str]:
        """Get all descendants (entire subtree)"""
        descendants = set()
        to_visit = [node_id]
        
        while to_visit:
            current = to_visit.pop()
            children = self.get_children(current)
            
            for child in children:
                if child not in descendants:
                    descendants.add(child)
                    to_visit.append(child)
        
        return descendants
    
    def get_leftmost_sibling(self, node_id: str) -> str:
        """Get first sibling in chain"""
        prev = self.traverse_direction(node_id, Direction.LEFT)
        return prev[-1] if prev else node_id
    
    def get_rightmost_sibling(self, node_id: str) -> str:
        """Get last sibling in chain"""
        next_nodes = self.traverse_direction(node_id, Direction.RIGHT)
        return next_nodes[-1] if next_nodes else node_id
    
    def move_node(
        self, 
        node_id: str, 
        new_parent_id: str,
        position: str = 'last'
    ) -> None:
        """
        Rewire node to new parent (dynamic re-scoping).
        
        Args:
            node_id: Node to move
            new_parent_id: New parent
            position: 'first', 'last', or sibling_id to insert after
        
        Example:
            # Failed exploit → jump back to parent and try lateral movement
            graph.move_node("exploit-node", "recon-parent")
        """
        with self.lock:
            # Remove from current parent
            old_parent = self.get_parent(node_id)
            if old_parent:
                self._unlink_from_siblings(node_id)
            
            # Add to new parent
            if position == 'first':
                # Become first child
                old_first = self.get_neighbor(new_parent_id, Direction.DOWN)
                self.add_edge(new_parent_id, Direction.DOWN, node_id, overwrite=True)
                if old_first:
                    self.add_edge(node_id, Direction.RIGHT, old_first, overwrite=True)
            
            elif position == 'last':
                # Append to end of sibling chain
                first_child = self.get_neighbor(new_parent_id, Direction.DOWN)
                if not first_child:
                    self.add_edge(new_parent_id, Direction.DOWN, node_id, overwrite=True)
                else:
                    rightmost = self.get_rightmost_sibling(first_child)
                    self.add_edge(rightmost, Direction.RIGHT, node_id, overwrite=True)
            
            else:
                # Insert after specific sibling
                self.add_edge(position, Direction.RIGHT, node_id, overwrite=True)
    
    def _unlink_from_siblings(self, node_id: str) -> None:
        """Internal: remove node from sibling chain"""
        left = self.get_neighbor(node_id, Direction.LEFT)
        right = self.get_neighbor(node_id, Direction.RIGHT)
        
        if left and right:
            # Middle of chain: connect left to right
            self.add_edge(left, Direction.RIGHT, right, overwrite=True)
        elif left:
            # Rightmost: break left's right link
            self.remove_edge(left, Direction.RIGHT)
        elif right:
            # Leftmost: update parent's DOWN link
            parent = self.get_parent(node_id)
            if parent:
                self.add_edge(parent, Direction.DOWN, right, overwrite=True)
        
        # Clear node's horizontal links
        self.relations[node_id][Direction.LEFT] = None
        self.relations[node_id][Direction.RIGHT] = None
    
    def remove_subtree(self, node_id: str) -> Set[str]:
        """
        Remove node and all descendants.
        
        Returns:
            Set of removed node IDs
        """
        with self.lock:
            # Get all nodes to remove
            to_remove = self.get_descendants(node_id)
            to_remove.add(node_id)
            
            # Unlink from parent
            self._unlink_from_siblings(node_id)
            parent = self.get_parent(node_id)
            if parent:
                self.remove_edge(parent, Direction.DOWN)
            
            # Remove all nodes
            for nid in to_remove:
                del self.relations[nid]
                if nid in self.nodes:
                    del self.nodes[nid]
            
            return to_remove
    
    def update_node_metadata(self, node_id: str, **updates) -> None:
        """Update node metadata (status, abstract, etc.)"""
        with self.lock:
            if node_id in self.nodes:
                self.nodes[node_id].update(updates)
    
    def get_node_metadata(self, node_id: str) -> dict:
        """Get node metadata"""
        with self.lock:
            return self.nodes.get(node_id, {}).copy()
    
    def to_dict(self) -> dict:
        """Export graph structure for debugging"""
        with self.lock:
            return {
                'nodes': {
                    nid: {
                        'metadata': self.nodes.get(nid, {}),
                        'edges': {
                            d.name: self.relations[nid][d]
                            for d in Direction
                        }
                    }
                    for nid in self.relations
                }
            }