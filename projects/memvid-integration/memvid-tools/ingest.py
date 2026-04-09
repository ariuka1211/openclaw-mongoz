#!/usr/bin/env python3
"""
Memvid ingestion wrapper for adding content to .mv2 files.
Handles batch ingestion of OpenClaw memory and session data.
"""

import os
import sys
from typing import Dict, List, Any, Optional, Union
from datetime import datetime, timezone
import json
import memvid_sdk


class MemvidIngestError(Exception):
    """Custom exception for memvid ingestion operations"""
    pass


class MemvidIngest:
    """Wrapper class for ingesting content into memvid files"""
    
    def __init__(self, mv_file_path: str, auto_create: bool = True):
        """
        Initialize MemvidIngest with a .mv2 file
        
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
            raise MemvidIngestError(f"Memvid file not found: {mv_file_path}")
    
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
    
    def add_document(self, 
                    title: str,
                    content: str,
                    label: str = "document",
                    metadata: Optional[Dict[str, Any]] = None,
                    timestamp: Optional[Union[str, int]] = None) -> str:
        """
        Add a single document to the memvid file
        
        Args:
            title: Document title
            content: Document content/text
            label: Document label/category
            metadata: Additional metadata
            timestamp: Document timestamp (ISO string or Unix timestamp)
            
        Returns:
            Frame ID of the added document
        """
        if not self.mv_instance:
            raise MemvidIngestError("Memvid instance not initialized")
        
        try:
            # Prepare metadata
            final_metadata = metadata or {}
            
            # Add ingestion timestamp if not provided
            if timestamp:
                if isinstance(timestamp, str):
                    # Assume ISO format string
                    final_metadata['timestamp'] = timestamp
                elif isinstance(timestamp, int):
                    # Unix timestamp
                    final_metadata['timestamp'] = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            else:
                final_metadata['ingestion_time'] = datetime.now(timezone.utc).isoformat()
            
            # Add document to memvid
            frame_id = self.mv_instance.put(
                title=title,
                label=label,
                text=content,
                metadata=final_metadata,
                timestamp=timestamp
            )
            
            return str(frame_id)
            
        except Exception as e:
            raise MemvidIngestError(f"Failed to add document: {e}")
    
    def add_session_data(self, 
                        session_id: str,
                        session_content: str,
                        session_date: str,
                        session_type: str = "session") -> str:
        """
        Add OpenClaw session data to memvid
        
        Args:
            session_id: Unique session identifier
            session_content: Session content (conversation, logs, etc.)
            session_date: Session date (YYYY-MM-DD format)
            session_type: Type of session data
            
        Returns:
            Frame ID of the added session
        """
        title = f"Session {session_date} ({session_id[:8]})"
        metadata = {
            'session_id': session_id,
            'session_date': session_date,
            'session_type': session_type,
            'source': 'openclaw_session'
        }
        
        return self.add_document(
            title=title,
            content=session_content,
            label='session',
            metadata=metadata
        )
    
    def add_memory_file(self, 
                       memory_file_path: str,
                       file_type: str = "memory") -> str:
        """
        Add content from a memory file (e.g., memory/*.md files)
        
        Args:
            memory_file_path: Path to the memory file
            file_type: Type of memory file
            
        Returns:
            Frame ID of the added memory
        """
        if not os.path.exists(memory_file_path):
            raise MemvidIngestError(f"Memory file not found: {memory_file_path}")
        
        try:
            with open(memory_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract filename for title
            filename = os.path.basename(memory_file_path)
            title = f"Memory: {filename}"
            
            # Get file stats
            stat = os.stat(memory_file_path)
            metadata = {
                'source_file': memory_file_path,
                'file_type': file_type,
                'file_size': stat.st_size,
                'modified_time': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                'source': 'openclaw_memory'
            }
            
            return self.add_document(
                title=title,
                content=content,
                label='memory',
                metadata=metadata
            )
            
        except Exception as e:
            raise MemvidIngestError(f"Failed to add memory file {memory_file_path}: {e}")
    
    def batch_ingest(self, documents: List[Dict[str, Any]]) -> List[str]:
        """
        Batch ingest multiple documents for performance
        
        Args:
            documents: List of document dictionaries with keys:
                      - title (required)
                      - content (required)  
                      - label (optional, default: 'document')
                      - metadata (optional)
                      
        Returns:
            List of frame IDs
        """
        if not self.mv_instance:
            raise MemvidIngestError("Memvid instance not initialized")
        
        try:
            # Prepare documents for memvid's put_many method
            memvid_docs = []
            for doc in documents:
                if 'title' not in doc or 'content' not in doc:
                    raise MemvidIngestError("Each document must have 'title' and 'content' keys")
                
                memvid_doc = {
                    'title': doc['title'],
                    'text': doc['content'],  # memvid uses 'text' not 'content'
                    'label': doc.get('label', 'document'),
                }
                
                if 'metadata' in doc:
                    memvid_doc['metadata'] = doc['metadata']
                
                memvid_docs.append(memvid_doc)
            
            # Use memvid's batch ingestion for performance
            frame_ids = self.mv_instance.put_many(memvid_docs)
            return [str(fid) for fid in frame_ids]
            
        except Exception as e:
            raise MemvidIngestError(f"Batch ingest failed: {e}")
    
    def get_frame_count(self) -> int:
        """Get the current number of frames in the memvid file"""
        if not self.mv_instance:
            raise MemvidIngestError("Memvid instance not initialized")
        
        try:
            stats = self.mv_instance.stats()
            return stats.get('active_frame_count', 0)
        except Exception as e:
            raise MemvidIngestError(f"Failed to get frame count: {e}")


def ingest_memory_directory(mv_file_path: str, 
                           memory_dir: str,
                           exclude_patterns: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Convenience function to ingest an entire memory directory
    
    Args:
        mv_file_path: Path to .mv2 file
        memory_dir: Path to memory directory
        exclude_patterns: List of filename patterns to exclude
        
    Returns:
        Dictionary with ingestion results
    """
    exclude_patterns = exclude_patterns or ['session.md']  # Default exclusions
    
    if not os.path.exists(memory_dir):
        raise MemvidIngestError(f"Memory directory not found: {memory_dir}")
    
    results = {
        'total_files': 0,
        'ingested_files': 0,
        'skipped_files': 0,
        'frame_ids': [],
        'errors': []
    }
    
    with MemvidIngest(mv_file_path) as ingestor:
        for filename in os.listdir(memory_dir):
            file_path = os.path.join(memory_dir, filename)
            
            # Skip if not a file or matches exclusion pattern
            if not os.path.isfile(file_path):
                continue
            
            results['total_files'] += 1
            
            # Check exclusion patterns
            skip = False
            for pattern in exclude_patterns:
                if pattern in filename:
                    results['skipped_files'] += 1
                    skip = True
                    break
            
            if skip:
                continue
            
            try:
                frame_id = ingestor.add_memory_file(file_path)
                results['frame_ids'].append(frame_id)
                results['ingested_files'] += 1
            except Exception as e:
                error_msg = f"Failed to ingest {filename}: {e}"
                results['errors'].append(error_msg)
    
    return results


if __name__ == "__main__":
    # CLI interface for testing
    if len(sys.argv) < 4:
        print("Usage: python ingest.py <mv_file_path> <title> <content> [label]")
        print("   or: python ingest.py <mv_file_path> --memory-dir <memory_dir>")
        sys.exit(1)
    
    mv_file = sys.argv[1]
    
    try:
        if len(sys.argv) > 3 and sys.argv[2] == '--memory-dir':
            # Ingest memory directory
            memory_dir = sys.argv[3]
            results = ingest_memory_directory(mv_file, memory_dir)
            print(f"Ingested {results['ingested_files']}/{results['total_files']} files")
            print(f"Frame IDs: {results['frame_ids']}")
            if results['errors']:
                print(f"Errors: {results['errors']}")
        else:
            # Single document ingest
            title = sys.argv[2]
            content = sys.argv[3]
            label = sys.argv[4] if len(sys.argv) > 4 else "document"
            
            with MemvidIngest(mv_file) as ingestor:
                frame_id = ingestor.add_document(title, content, label)
                print(f"Added document with frame ID: {frame_id}")
                print(f"Total frames: {ingestor.get_frame_count()}")
                
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)