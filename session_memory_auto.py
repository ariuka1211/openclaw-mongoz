#!/usr/bin/env python3
"""
OpenClaw Memvid Auto-Integration
Lightweight session startup and wrap-up with semantic memory.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add memvid tools to path
workspace_root = Path("/root/.openclaw/workspace")
memvid_tools_path = workspace_root / "projects" / "memvid-integration" / "memvid-tools"
sys.path.insert(0, str(memvid_tools_path))

from search import MemvidSearch
from ingest import MemvidIngest

MEMVID_FILE = workspace_root / "workspace-memory.mv2"

class SessionMemoryIntegration:
    """Lightweight session memory with automatic context loading."""
    
    def __init__(self):
        self.session_start_time = datetime.now(timezone.utc)
        self.session_content = []
        
    def contextual_search(self, query, k=3, min_score=5.0):
        """Search for relevant context during conversation."""
        if not MEMVID_FILE.exists():
            return []
            
        try:
            import memvid_sdk
            with memvid_sdk.use("basic", str(MEMVID_FILE)) as mv:
                results = mv.find(query=query, k=k)
                
                relevant = []
                for hit in results.get('hits', []):
                    if hit.get('score', 0) >= min_score:
                        relevant.append({
                            'title': hit.get('title', 'Unknown'),
                            'snippet': hit.get('snippet', hit.get('content', ''))[:300],
                            'score': hit.get('score', 0),
                            'source': 'memvid_context'
                        })
                
                return relevant
                
        except Exception as e:
            print(f"Context search failed: {e}")
            return []
    
    def add_conversation_context(self, user_msg, assistant_msg):
        """Track conversation for later ingestion."""
        self.session_content.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'user': user_msg,
            'assistant': assistant_msg
        })
    
    def ingest_session(self, session_title=None):
        """Ingest current session content into memvid."""
        if not self.session_content:
            return False
            
        try:
            # Format session content
            content_parts = []
            for exchange in self.session_content:
                content_parts.append(f"User: {exchange['user']}")
                content_parts.append(f"Assistant: {exchange['assistant']}")
                content_parts.append("---")
            
            full_content = "\n".join(content_parts)
            
            if len(full_content.strip()) < 100:
                return False  # Too short to be valuable
            
            title = session_title or f"Session {self.session_start_time.strftime('%Y-%m-%d %H:%M UTC')}"
            
            with MemvidIngest(str(MEMVID_FILE)) as ingester:
                success = ingester.add_document(
                    title=title,
                    content=full_content,
                    label="auto_session",
                    metadata={
                        "type": "auto_session",
                        "start_time": self.session_start_time.isoformat(),
                        "end_time": datetime.now(timezone.utc).isoformat(),
                        "exchange_count": len(self.session_content)
                    }
                )
                
                if success:
                    print(f"📝 Auto-ingested session: {len(self.session_content)} exchanges")
                
                return success
                
        except Exception as e:
            print(f"Warning: Auto-ingestion failed: {e}")
            return False

# Global session instance for easy access
_session_memory = None

def get_session_memory():
    """Get or create session memory instance."""
    global _session_memory
    if _session_memory is None:
        _session_memory = SessionMemoryIntegration()
    return _session_memory

def search_context(query, k=3):
    """Quick context search - use during conversations."""
    memory = get_session_memory()
    return memory.contextual_search(query, k=k)

def track_exchange(user_msg, assistant_msg):
    """Track conversation exchange for later ingestion."""
    memory = get_session_memory()
    memory.add_conversation_context(user_msg, assistant_msg)

def wrap_up_session(session_title=None):
    """Ingest session and reset for next session."""
    global _session_memory
    if _session_memory:
        success = _session_memory.ingest_session(session_title)
        _session_memory = None  # Reset for next session
        return success
    return False

if __name__ == "__main__":
    # Demo usage
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        results = search_context(query, k=3)
        
        print(f"🔍 Context search: '{query}'")
        print(f"Found {len(results)} relevant items:")
        print()
        
        for i, result in enumerate(results, 1):
            print(f"{i}. {result['title']} (score: {result['score']:.1f})")
            print(f"   {result['snippet'][:150]}...")
            print()
    else:
        print("Usage: python3 session_memory_auto.py <search_query>")
        print("Or import: from session_memory_auto import search_context, track_exchange, wrap_up_session")