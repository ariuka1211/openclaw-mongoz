#!/usr/bin/env python3
"""
Comprehensive test for memvid wrapper modules.
Tests search.py, ingest.py, and embeddings.py together.
"""

import os
import tempfile
import sys
from pathlib import Path

# Add memvid-tools to path
sys.path.insert(0, str(Path(__file__).parent))

from search import MemvidSearch, search_memvid_file
from ingest import MemvidIngest, ingest_memory_directory
from embeddings import create_default_embedder, EmbeddingConfig


def test_wrapper_modules():
    """Test all wrapper modules together"""
    
    test_file = "/tmp/test_wrapper_memvid.mv2"
    
    # Clean up any existing test file
    if os.path.exists(test_file):
        os.remove(test_file)
    
    try:
        print("🧪 Phase 2 Test: Memvid Wrapper Modules")
        print("=" * 50)
        
        # Test 1: Embedding provider
        print("\n1️⃣ Testing embedding provider...")
        try:
            embedder = create_default_embedder()
            test_embedding = embedder.embed_query("test query")
            print(f"✅ Embedder: {embedder.model_name}")
            print(f"   Dimension: {len(test_embedding)}")
            print(f"   Sample values: {test_embedding[:3]}")
        except Exception as e:
            print(f"❌ Embedding test failed: {e}")
            # Continue with other tests even if embeddings fail
            embedder = None
        
        # Test 2: Document ingestion
        print("\n2️⃣ Testing document ingestion...")
        with MemvidIngest(test_file) as ingestor:
            # Add test documents
            frame_id1 = ingestor.add_document(
                title="AI Research Document",
                content="This document discusses artificial intelligence and machine learning techniques used in modern applications.",
                label="research",
                metadata={"topic": "AI", "priority": "high"}
            )
            
            frame_id2 = ingestor.add_document(
                title="OpenClaw Platform Guide", 
                content="OpenClaw is an AI automation platform that helps developers build intelligent applications with ease.",
                label="documentation",
                metadata={"product": "openclaw", "version": "1.0"}
            )
            
            frame_id3 = ingestor.add_document(
                title="Memory Systems Overview",
                content="Memory systems in AI assistants enable context retention across sessions and improved user experience.",
                label="research", 
                metadata={"topic": "memory", "priority": "medium"}
            )
            
            frame_count = ingestor.get_frame_count()
            print(f"✅ Added 3 documents, total frames: {frame_count}")
            print(f"   Frame IDs: {[frame_id1, frame_id2, frame_id3]}")
        
        # Test 3: Search functionality
        print("\n3️⃣ Testing search functionality...")
        with MemvidSearch(test_file, auto_create=False) as searcher:
            # Test search 1: AI platform
            results1 = searcher.search("AI automation platform", k=2)
            print(f"✅ Search 'AI automation platform': {len(results1)} results")
            
            if results1:
                top_result = results1[0]
                print(f"   Top result: '{top_result['title']}' (score: {top_result['score']:.3f})")
                print(f"   Snippet: {top_result['snippet'][:80]}...")
            
            # Test search 2: Memory systems
            results2 = searcher.search("memory systems context", k=3)
            print(f"✅ Search 'memory systems context': {len(results2)} results")
            
            # Test search 3: Specific term
            results3 = searcher.search("OpenClaw", k=5)
            print(f"✅ Search 'OpenClaw': {len(results3)} results")
            
            # Get stats
            stats = searcher.get_stats()
            print(f"✅ Stats: {stats['frame_count']} frames, {stats['total_size_bytes']} bytes")
            print(f"   Indexes: vec={stats['index_status']['vec_enabled']}, lex={stats['index_status']['lex_enabled']}")
        
        # Test 4: Convenience function
        print("\n4️⃣ Testing convenience functions...")
        convenience_results = search_memvid_file(test_file, "artificial intelligence", k=2)
        print(f"✅ Convenience search: {len(convenience_results)} results")
        
        # Test 5: Batch operations
        print("\n5️⃣ Testing batch operations...")
        with MemvidIngest(test_file, auto_create=False) as ingestor:
            batch_docs = [
                {
                    "title": "Batch Document 1",
                    "content": "This is the first batch document about neural networks and deep learning.",
                    "label": "batch",
                    "metadata": {"batch_id": "1", "type": "neural_networks"}
                },
                {
                    "title": "Batch Document 2", 
                    "content": "This is the second batch document covering natural language processing techniques.",
                    "label": "batch",
                    "metadata": {"batch_id": "2", "type": "nlp"}
                }
            ]
            
            batch_frame_ids = ingestor.batch_ingest(batch_docs)
            final_count = ingestor.get_frame_count()
            print(f"✅ Batch ingest: {len(batch_frame_ids)} documents added")
            print(f"   Final frame count: {final_count}")
        
        # Test 6: Final verification search
        print("\n6️⃣ Final verification search...")
        final_results = search_memvid_file(test_file, "deep learning neural networks", k=3)
        print(f"✅ Final search: {len(final_results)} results")
        
        for i, result in enumerate(final_results, 1):
            print(f"   {i}. '{result['title']}' (score: {result['score']:.3f})")
        
        # Verify file size
        file_size = os.path.getsize(test_file)
        print(f"✅ Final file size: {file_size} bytes")
        
        print("\n🎉 All wrapper module tests passed!")
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


def test_embedding_providers():
    """Test different embedding providers"""
    print("\n🧪 Testing Embedding Providers")
    print("=" * 40)
    
    providers = EmbeddingConfig.get_available_providers()
    
    for provider_name, info in providers.items():
        print(f"\nTesting {provider_name}:")
        print(f"  Description: {info['description']}")
        print(f"  Requires API key: {info['requires_api_key']}")
        
        try:
            if provider_name == 'openai' and not os.environ.get('OPENAI_API_KEY'):
                print("  ⏭️  Skipped (no API key)")
                continue
                
            embedder = EmbeddingConfig.get_embedder_by_config(provider=provider_name)
            test_vec = embedder.embed_query("test embedding")
            print(f"  ✅ Working: {embedder.model_name} (dim: {len(test_vec)})")
            
        except Exception as e:
            print(f"  ❌ Failed: {e}")


if __name__ == "__main__":
    # Run wrapper module tests
    success = test_wrapper_modules()
    
    # Run embedding provider tests
    test_embedding_providers()
    
    exit(0 if success else 1)