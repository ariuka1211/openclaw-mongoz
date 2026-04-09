#!/usr/bin/env python3
"""Test script to verify memvid-sdk basic functionality"""

import os
import tempfile
import memvid_sdk

def test_basic_memvid():
    # Create a temporary test file
    test_file = "/tmp/test_memvid.mv2"
    
    # Clean up any existing test file
    if os.path.exists(test_file):
        os.remove(test_file)
    
    try:
        # Test 1: Create a new memvid instance
        print("🧪 Test 1: Creating memvid instance...")
        mv = memvid_sdk.create(test_file)
        print("✅ Successfully created memvid instance")
        
        # Test 2: Add some test records using put method
        print("\n🧪 Test 2: Adding test records...")
        frame_id1 = mv.put(
            title="Test Document 1",
            label="document",
            text="This is a test document about artificial intelligence and machine learning."
        )
        frame_id2 = mv.put(
            title="Test Document 2",
            label="document",
            text="OpenClaw is an AI automation platform for developers."
        )
        frame_id3 = mv.put(
            title="Test Document 3",
            label="document",
            text="Memory systems help AI assistants maintain context across sessions."
        )
        print(f"✅ Successfully added 3 test records (IDs: {frame_id1}, {frame_id2}, {frame_id3})")
        
        # Test 3: Search functionality using find method
        print("\n🧪 Test 3: Testing search...")
        results = mv.find("AI automation platform", k=2)
        print(f"✅ Search returned {len(results.get('hits', []))} results")
        
        if results.get('hits'):
            for i, result in enumerate(results['hits']):
                print(f"  Result {i+1}: {result.get('title', 'No title')} (score: {result.get('score', 0):.3f})")
                preview = result.get('content', '')[:50] if result.get('content') else ''
                print(f"    Content preview: {preview}...")
        
        # Test 4: Verify file exists and has content
        print(f"\n🧪 Test 4: Verifying file...")
        file_size = os.path.getsize(test_file)
        print(f"✅ File created successfully: {test_file} ({file_size} bytes)")
        
        # Test 5: Get stats to verify content was added
        print("\n🧪 Test 5: Getting stats...")
        stats = mv.stats()
        frame_count = stats.get('active_frame_count', 0)
        print(f"✅ Stats retrieved: {frame_count} active frames")
        
        # Close the memvid instance
        mv.close()
        print("✅ Memvid instance closed successfully")
        
        print("\n🎉 All basic tests passed!")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Clean up
        if os.path.exists(test_file):
            os.remove(test_file)
            print("🧹 Cleaned up test file")

if __name__ == "__main__":
    success = test_basic_memvid()
    exit(0 if success else 1)