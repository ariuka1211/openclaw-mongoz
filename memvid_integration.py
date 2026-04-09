#!/usr/bin/env python3
"""
OpenClaw Memvid Integration — Session Integration Script
Adds memvid semantic search to session startup and wrap-up without modifying OpenClaw core.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

# Add memvid tools to path
memvid_tools_path = Path(__file__).parent / "projects" / "memvid-integration" / "memvid-tools"
sys.path.insert(0, str(memvid_tools_path))

from search import MemvidSearch
from ingest import MemvidIngest

# Workspace and memory paths
WORKSPACE_ROOT = Path("/root/.openclaw/workspace")
MEMORY_DIR = WORKSPACE_ROOT / "memory"
MEMVID_FILE = WORKSPACE_ROOT / "workspace-memory.mv2"
SESSION_MD = MEMORY_DIR / "session.md"

def memvid_search_enhanced(query, k=5, min_score=5.0):
    """
    Enhanced semantic search using memvid.
    Returns results in OpenClaw memory_search compatible format.
    """
    if not MEMVID_FILE.exists():
        return {"results": [], "provider": "memvid", "error": "No memvid file found"}
    
    try:
        with MemvidSearch(str(MEMVID_FILE), auto_create=False) as searcher:
            # Force vector search mode to avoid lex index dependency
            results = []
            
            # Try vector search first
            try:
                import memvid_sdk
                mv = memvid_sdk.use("basic", str(MEMVID_FILE))
                raw_results = mv.find(query=query, k=k, mode='vector')
                
                for hit in raw_results.get('hits', []):
                    if hit.get('score', 0) >= min_score:
                        # Format to match OpenClaw's memory_search format
                        result = {
                            "path": hit.get('title', 'Unknown'),
                            "startLine": 1,
                            "endLine": 10,
                            "score": hit.get('score', 0),
                            "snippet": hit.get('snippet', hit.get('content', ''))[:200],
                            "source": "memvid",
                            "citation": f"memvid#{hit.get('frame_id', '')}"
                        }
                        results.append(result)
                mv.close()
                
            except Exception as e:
                return {"results": [], "provider": "memvid", "error": f"Search failed: {e}"}
            
            return {
                "results": results,
                "provider": "memvid", 
                "model": "BGE_SMALL",
                "citations": "auto"
            }
            
    except Exception as e:
        return {"results": [], "provider": "memvid", "error": f"Memvid access failed: {e}"}

def memvid_ingest_session(session_content, session_title=None):
    """
    Ingest current session into memvid for future semantic search.
    """
    if not session_content or len(session_content.strip()) < 50:
        return False
    
    try:
        with MemvidIngest(str(MEMVID_FILE)) as ingester:
            title = session_title or f"Session {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            
            success = ingester.ingest_document(
                title=title,
                content=session_content,
                metadata={
                    "type": "session", 
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "openclaw_session"
                },
                labels=["session", "openclaw"]
            )
            
            return success
            
    except Exception as e:
        print(f"Warning: Failed to ingest session to memvid: {e}")
        return False

def demo_memvid_search():
    """Demo function to test memvid search from command line."""
    if len(sys.argv) < 2:
        print("Usage: python memvid_integration.py <search_query>")
        sys.exit(1)
    
    query = " ".join(sys.argv[1:])
    print(f"🔍 Searching memvid for: '{query}'")
    
    results = memvid_search_enhanced(query, k=5, min_score=1.0)
    
    if results.get("error"):
        print(f"❌ Error: {results['error']}")
        sys.exit(1)
    
    hits = results.get("results", [])
    print(f"✅ Found {len(hits)} results:")
    
    for i, hit in enumerate(hits, 1):
        print(f"\n{i}. {hit['path']}")
        print(f"   Score: {hit['score']:.2f}")
        print(f"   {hit['snippet'][:120]}...")

if __name__ == "__main__":
    demo_memvid_search()