#!/usr/bin/env python3
"""
Embedding provider configuration for Memvid integration.
Provides local BGE_SMALL embeddings as default to avoid API key dependencies.
"""

import os
from typing import Optional, Dict, Any
from memvid_sdk.embeddings import (
    EmbeddingProvider,
    HuggingFaceEmbeddings,
    OpenAIEmbeddings,
    HashEmbeddings,
    get_embedder
)


class EmbeddingConfig:
    """Configuration class for embedding providers"""
    
    # Default configuration prioritizing local models
    DEFAULT_CONFIG = {
        'provider': 'huggingface',
        'model': 'BAAI/bge-small-en-v1.5',  # BGE_SMALL equivalent 
        'fallback_provider': 'hash',
        'auto_fallback': True
    }
    
    @classmethod
    def get_default_embedder(cls, **kwargs) -> EmbeddingProvider:
        """
        Get the default embedding provider (local BGE_SMALL)
        
        Args:
            **kwargs: Additional configuration options
            
        Returns:
            EmbeddingProvider instance
        """
        config = cls.DEFAULT_CONFIG.copy()
        config.update(kwargs)
        
        try:
            # Try local BGE_SMALL first (no API key required)
            return HuggingFaceEmbeddings(model=config['model'])
        except Exception as e:
            if config.get('auto_fallback', True):
                print(f"Warning: Failed to load {config['model']}, falling back to hash embeddings: {e}")
                return HashEmbeddings(dimension=384)  # Match BGE_SMALL dimension
            else:
                raise
    
    @classmethod
    def get_embedder_by_config(cls, 
                              provider: str = 'huggingface',
                              model: Optional[str] = None,
                              api_key: Optional[str] = None,
                              **kwargs) -> EmbeddingProvider:
        """
        Get embedding provider by configuration
        
        Args:
            provider: Provider name ('huggingface', 'openai', 'hash', etc.)
            model: Model name (uses provider default if not specified)
            api_key: API key for cloud providers
            **kwargs: Additional provider-specific options
            
        Returns:
            EmbeddingProvider instance
        """
        if provider == 'huggingface':
            # Local BGE models
            model = model or 'BAAI/bge-small-en-v1.5'
            return HuggingFaceEmbeddings(model=model, **kwargs)
        
        elif provider == 'openai':
            # OpenAI embeddings (requires API key)
            if not api_key:
                api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OpenAI API key required for OpenAI embeddings")
            
            model = model or 'text-embedding-3-small'
            return OpenAIEmbeddings(api_key=api_key, model=model, **kwargs)
        
        elif provider == 'hash':
            # Hash embeddings (deterministic, no models needed)
            dimension = kwargs.get('dimension', 384)  # BGE_SMALL dimension
            return HashEmbeddings(dimension=dimension)
        
        else:
            # Use generic factory function
            return get_embedder(provider=provider, model=model, api_key=api_key, **kwargs)
    
    @classmethod
    def get_available_providers(cls) -> Dict[str, Dict[str, Any]]:
        """
        Get information about available embedding providers
        
        Returns:
            Dictionary with provider information
        """
        return {
            'huggingface': {
                'description': 'Local HuggingFace models (recommended)',
                'requires_api_key': False,
                'default_model': 'BAAI/bge-small-en-v1.5',
                'dimension': 384,
                'examples': [
                    'BAAI/bge-small-en-v1.5',  # BGE_SMALL
                    'BAAI/bge-base-en-v1.5',   # BGE_BASE
                    'all-MiniLM-L6-v2'
                ]
            },
            'openai': {
                'description': 'OpenAI cloud embeddings',
                'requires_api_key': True,
                'env_var': 'OPENAI_API_KEY',
                'default_model': 'text-embedding-3-small',
                'dimension': 1536,
                'examples': [
                    'text-embedding-3-small',
                    'text-embedding-3-large',
                    'text-embedding-ada-002'
                ]
            },
            'hash': {
                'description': 'Deterministic hash embeddings (fallback)',
                'requires_api_key': False,
                'default_model': 'memvid-hash-384',
                'dimension': 384,
                'examples': ['memvid-hash-384']
            }
        }
    
    @classmethod
    def validate_provider(cls, provider: str, **config) -> bool:
        """
        Validate that a provider can be used with given config
        
        Args:
            provider: Provider name
            **config: Provider configuration
            
        Returns:
            True if provider is available and configured correctly
        """
        try:
            embedder = cls.get_embedder_by_config(provider=provider, **config)
            # Test with a simple embedding
            test_embedding = embedder.embed_query("test")
            return len(test_embedding) > 0
        except Exception as e:
            print(f"Provider validation failed for {provider}: {e}")
            return False


def create_default_embedder() -> EmbeddingProvider:
    """
    Create the default embedding provider for OpenClaw memvid integration.
    Prioritizes local BGE_SMALL to avoid API key dependencies.
    
    Returns:
        EmbeddingProvider instance
    """
    return EmbeddingConfig.get_default_embedder()


def create_embedder_from_env() -> EmbeddingProvider:
    """
    Create embedding provider based on environment variables.
    Allows users to override defaults via environment.
    
    Environment variables:
        MEMVID_EMBEDDING_PROVIDER: Provider name (default: huggingface)
        MEMVID_EMBEDDING_MODEL: Model name (provider default if not set)
        OPENAI_API_KEY: Required for OpenAI provider
        
    Returns:
        EmbeddingProvider instance
    """
    provider = os.environ.get('MEMVID_EMBEDDING_PROVIDER', 'huggingface')
    model = os.environ.get('MEMVID_EMBEDDING_MODEL')
    
    # Check for API keys in environment
    api_key = None
    if provider == 'openai':
        api_key = os.environ.get('OPENAI_API_KEY')
    
    return EmbeddingConfig.get_embedder_by_config(
        provider=provider,
        model=model,
        api_key=api_key
    )


if __name__ == "__main__":
    # Test embedding providers
    import sys
    
    # Test default embedder
    print("🧪 Testing default embedder (BGE_SMALL)...")
    try:
        embedder = create_default_embedder()
        test_vec = embedder.embed_query("Hello world")
        print(f"✅ Default embedder: {embedder.model_name} (dimension: {len(test_vec)})")
    except Exception as e:
        print(f"❌ Default embedder failed: {e}")
    
    # Test available providers
    print("\n📋 Available providers:")
    providers = EmbeddingConfig.get_available_providers()
    for name, info in providers.items():
        status = "✅" if EmbeddingConfig.validate_provider(name) else "❌"
        api_key_info = f" (needs {info.get('env_var', 'API_KEY')})" if info['requires_api_key'] else ""
        print(f"  {status} {name}: {info['description']}{api_key_info}")
    
    # Test environment-based config
    print("\n🌍 Testing environment-based configuration...")
    try:
        env_embedder = create_embedder_from_env()
        test_vec = env_embedder.embed_query("Environment test")
        print(f"✅ Environment embedder: {env_embedder.model_name} (dimension: {len(test_vec)})")
    except Exception as e:
        print(f"❌ Environment embedder failed: {e}")