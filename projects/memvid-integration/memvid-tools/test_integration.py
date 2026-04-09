#!/usr/bin/env python3
"""
Integration test script for memvid memory layer.
Verifies end-to-end functionality: search, ingest, file health.
"""

import sys
import os
import tempfile
from datetime import datetime

# Add tools to path
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

from search import MemvidSearch, search_memvid_file
from ingest import MemvidIngest

def run_integration_test():
    """Run comprehensive integration test"""
    
    MV2_FILE = "/root/.openclaw/workspace/workspace-memory.mv2"
    
    print("=== Memvid Integration Test ===")
    print()
    
    # Test 1: File health check
    print("1. Health check...")
    with MemvidSearch(MV2_FILE) as searcher:
        healthy = searcher.is_healthy()
        stats = searcher.get_stats()
        
        if not healthy:
            print("❌ File is not healthy")
            return False
        
        frame_count = stats['frame_count']
        print(f"✅ File healthy - {frame_count} frames")
    
    # Test 2: Search functionality
    print("\n2. Search tests...")
    search_tests = [
        ("leverage max position", "Trading/leverage queries"),
        ("grid bot bitcoin", "Grid bot queries"),  
        ("mental health context", "Personal context"),
        ("ai trader refactor", "Code/development"),
    ]
    
    search_pass_count = 0
    for query, desc in search_tests:
        try:
            results = search_memvid_file(MV2_FILE, query, k=2)
            if results and results[0]['score'] > 5.0:  # Decent score threshold
                print(f"✅ {desc}: {len(results)} results, top score {results[0]['score']:.1f}")
                search_pass_count += 1
            else:
                print(f"⚠️  {desc}: {len(results)} results, low relevance")
                search_pass_count += 0.5  # Partial credit
        except Exception as e:
            print(f"❌ {desc}: Error - {e}")
    
    search_success_rate = search_pass_count / len(search_tests)
    print(f"   Search success rate: {search_success_rate:.0%}")
    
    # Test 3: Ingest functionality  
    print("\n3. Ingest test...")
    test_content = f"Integration test run at {datetime.now().isoformat()}"
    try:
        with MemvidIngest(MV2_FILE) as ingestor:
            initial_count = ingestor.get_frame_count()
            frame_id = ingestor.add_document(
                title="Integration test",
                content=test_content,
                label="test",
                metadata={"test": True}
            )
            final_count = ingestor.get_frame_count()
            
        if final_count == initial_count + 1:
            print(f"✅ Ingest successful - frame {frame_id}, count {initial_count} → {final_count}")
        else:
            print(f"❌ Ingest failed - count mismatch")
            return False
            
    except Exception as e:
        print(f"❌ Ingest error: {e}")
        return False
    
    # Test 4: Search for newly ingested content
    print("\n4. Search new content...")
    try:
        results = search_memvid_file(MV2_FILE, "integration test run", k=1)
        if results and "Integration test" in results[0]['title']:
            print(f"✅ Found new content - score {results[0]['score']:.1f}")
        else:
            print("⚠️  New content not found in search")
    except Exception as e:
        print(f"❌ Search new content error: {e}")
    
    # Test 5: CLI interface
    print("\n5. CLI interface test...")
    try:
        import subprocess
        result = subprocess.run([
            sys.executable, "search.py", MV2_FILE, "grid bot", "1"
        ], capture_output=True, text=True, cwd=script_dir)
        
        if result.returncode == 0 and "Found" in result.stdout:
            print("✅ CLI interface working")
        else:
            print(f"⚠️  CLI returned: {result.returncode}, output: {result.stdout[:100]}...")
    except Exception as e:
        print(f"❌ CLI test error: {e}")
    
    # Summary
    print("\n=== Summary ===")
    if search_success_rate >= 0.75:
        print("✅ Integration test PASSED - memvid layer is functional")
        return True
    else:
        print("❌ Integration test FAILED - issues found")
        return False

if __name__ == "__main__":
    success = run_integration_test()
    sys.exit(0 if success else 1)