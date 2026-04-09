#!/usr/bin/env python3
"""
Memvid search wrapper for semantic search over .mv2 files.
Integrates with OpenClaw's memory system to provide enhanced search capabilities.
"""

import os
import sys
from typing import Dict, List, Any, Optional, Union
import memvid_sdk


class MemvidSearchError(Exception):
    """Custom exception for memvid search operations"""
    pass


class MemvidSearch:
    """Wrapper class for semantic search operations on memvid files"""
    
    def __init__(self, mv_file_path: str, auto_create: bool = True):
        """
        Initialize MemvidSearch with a .mv2 file
        
        Args:
            mv_file_path: Path to the .mv2 file
            auto_create: If True, create the file if it doesn't exist
        """
        self.mv_file_path = mv_file_path
        self.mv_instance = None
        
        if not os.path.exists(mv_file_path) and auto_create:
            # Create new memvid file
            self.mv_instance = memvid_sdk.create(mv_file_path)
        elif os.path.exists(mv_file_path):
            # Open existing file
            self.mv_instance = memvid_sdk.use("basic", mv_file_path)
        else:
            raise MemvidSearchError(f"Memvid file not found: {mv_file_path}")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
    
    def close(self):
        """Close the memvid instance"""
        if self.mv_instance:
            self.mv_instance.close()
            self.mv_instance = None
    
    def search(self, 
               query: str, 
               k: int = 10,
               mode: Optional[str] = None,
               min_score: float = 0.0,
               snippet_chars: int = 480) -> List[Dict[str, Any]]:
        """
        Perform semantic search on the memvid file
        
        Args:
            query: Search query string
            k: Number of results to return (default: 10)
            mode: Search mode (None for auto-detection)
            min_score: Minimum score threshold
            snippet_chars: Number of characters for snippets
            
        Returns:
            List of search results with standardized format:
            [
                {
                    'title': str,
                    'content': str,
                    'score': float,
                    'frame_id': str,
                    'metadata': dict,
                    'snippet': str
                },
                ...
            ]
        """
        if not self.mv_instance:
            raise MemvidSearchError("Memvid instance not initialized")
        
        try:
            # Perform search using memvid's find method
            result = self.mv_instance.find(
                query=query,
                k=k,
                mode=mode,
                snippet_chars=snippet_chars
            )
            
            # Convert to standardized format
            standardized_results = []
            hits = result.get('hits', [])
            
            for hit in hits:
                score = hit.get('score', 0.0)
                
                # Apply minimum score filter
                if score < min_score:
                    continue
                
                standardized_result = {
                    'title': hit.get('title', 'Untitled'),
                    'content': hit.get('content', ''),
                    'score': score,
                    'frame_id': str(hit.get('frame_id', '')),
                    'metadata': hit.get('metadata', {}),
                    'snippet': hit.get('snippet', hit.get('content', '')[:snippet_chars])
                }
                standardized_results.append(standardized_result)
            
            return standardized_results
            
        except Exception as e:
            raise MemvidSearchError(f"Search failed: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the memvid file"""
        if not self.mv_instance:
            raise MemvidSearchError("Memvid instance not initialized")
        
        try:
            stats = self.mv_instance.stats()
            return {
                'frame_count': stats.get('active_frame_count', 0),
                'total_size_bytes': stats.get('file_size_bytes', 0),
                'index_status': {
                    'vec_enabled': stats.get('vec_index_enabled', False),
                    'lex_enabled': stats.get('lex_index_enabled', False)
                }
            }
        except Exception as e:
            raise MemvidSearchError(f"Failed to get stats: {e}")
    
    def is_healthy(self) -> bool:
        """Check if the memvid file is accessible and healthy"""
        try:
            stats = self.get_stats()
            return stats['frame_count'] >= 0  # Basic sanity check
        except:
            return False


def search_memvid_file(mv_file_path: str, 
                      query: str, 
                      k: int = 10,
                      min_score: float = 0.0) -> List[Dict[str, Any]]:
    """
    Convenience function for one-off searches
    
    Args:
        mv_file_path: Path to .mv2 file
        query: Search query
        k: Number of results
        min_score: Minimum score threshold
        
    Returns:
        List of search results
    """
    with MemvidSearch(mv_file_path, auto_create=False) as searcher:
        return searcher.search(query=query, k=k, min_score=min_score)


if __name__ == "__main__":
    # CLI interface for testing
    if len(sys.argv) < 3:
        print("Usage: python search.py <mv_file_path> <query> [k] [min_score]")
        sys.exit(1)
    
    mv_file = sys.argv[1]
    query = sys.argv[2]
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    min_score = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    
    try:
        results = search_memvid_file(mv_file, query, k, min_score)
        
        print(f"Found {len(results)} results for query: '{query}'\n")
        
        for i, result in enumerate(results, 1):
            print(f"Result {i}:")
            print(f"  Title: {result['title']}")
            print(f"  Score: {result['score']:.3f}")
            print(f"  Frame ID: {result['frame_id']}")
            print(f"  Snippet: {result['snippet'][:100]}...")
            print()
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)