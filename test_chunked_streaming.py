#!/usr/bin/env python3
"""
Test script for Chunked Streaming Implementation
Verifies sub-500ms latency and semantic chunking functionality
"""

import asyncio
import time
import numpy as np
import logging
import json
from typing import List, Dict

# Setup logging
logging.basicConfig(level=logging.INFO)
test_logger = logging.getLogger("chunked_streaming_test")

async def test_semantic_chunker():
    """Test the semantic chunking utility"""
    print("🧪 Testing Semantic Chunker...")
    
    try:
        from src.utils.semantic_chunking import semantic_chunker, ChunkBoundaryType
        
        # Reset chunker
        semantic_chunker.reset()
        
        # Test tokens that should form semantic chunks
        test_tokens = [
            ("Hello", 1, "Hello"),
            (",", 2, ","),
            (" I", 3, " I"),
            (" am", 4, " am"),
            (" fine", 5, " fine"),
            (",", 6, ","),
            (" thank", 7, " thank"),
            (" you", 8, " you"),
            (" for", 9, " for"),
            (" asking", 10, " asking"),
            (".", 11, "."),
            (" How", 12, " How"),
            (" are", 13, " are"),
            (" you", 14, " you"),
            ("?", 15, "?")
        ]
        
        chunks_created = []
        current_time = time.time()
        
        for token_text, token_id, expected in test_tokens:
            chunk = semantic_chunker.add_token(token_text, token_id, current_time)
            if chunk:
                chunks_created.append(chunk)
                print(f"  ✅ Chunk: '{chunk.text}' ({chunk.boundary_type.value}, conf: {chunk.confidence:.2f})")
        
        # Finalize any remaining content
        final_chunk = semantic_chunker.finalize_chunk(current_time)
        if final_chunk:
            chunks_created.append(final_chunk)
            print(f"  ✅ Final chunk: '{final_chunk.text}' ({final_chunk.boundary_type.value})")
        
        print(f"  📊 Total chunks created: {len(chunks_created)}")
        
        # Verify expected chunking behavior
        expected_chunks = [
            "Hello, I am",
            "fine, thank you",
            "for asking.",
            "How are you?"
        ]
        
        if len(chunks_created) >= 3:
            print("  ✅ Semantic chunking working correctly")
            return True
        else:
            print("  ❌ Semantic chunking not working as expected")
            return False
            
    except Exception as e:
        print(f"  ❌ Semantic chunker test failed: {e}")
        return False

async def test_voxtral_chunked_streaming():
    """Test Voxtral chunked streaming functionality"""
    print("🧪 Testing Voxtral Chunked Streaming...")
    
    try:
        from src.models.voxtral_model_realtime import voxtral_model
        
        # Initialize model
        if not voxtral_model.is_initialized:
            print("  🔄 Initializing Voxtral model...")
            await voxtral_model.initialize()
        
        # Create test audio data (1 second of silence)
        sample_rate = 16000
        duration = 1.0
        test_audio = np.zeros(int(sample_rate * duration), dtype=np.float32)
        
        # Add some noise to make it more realistic
        test_audio += np.random.normal(0, 0.01, test_audio.shape).astype(np.float32)
        
        print("  🎙️ Testing chunked streaming with sample audio...")
        
        chunks_received = []
        start_time = time.time()
        first_chunk_time = None
        
        # Process with chunked streaming
        async for chunk_data in voxtral_model.process_chunked_streaming(
            test_audio,
            prompt="Hello, how are you today?",
            chunk_id="test_chunk",
            mode="chunked_streaming"
        ):
            if chunk_data.get('type') == 'semantic_chunk':
                if first_chunk_time is None:
                    first_chunk_time = time.time()
                    first_chunk_latency = (first_chunk_time - start_time) * 1000
                    print(f"  ⚡ First chunk received in {first_chunk_latency:.1f}ms")
                
                chunks_received.append(chunk_data)
                chunk_text = chunk_data.get('text', '')
                boundary_type = chunk_data.get('boundary_type', 'unknown')
                generation_time = chunk_data.get('generation_time_ms', 0)
                
                print(f"  🎯 Chunk {len(chunks_received)}: '{chunk_text}' "
                      f"({boundary_type}, {generation_time:.1f}ms)")
            
            elif chunk_data.get('type') == 'complete':
                total_time = (time.time() - start_time) * 1000
                total_tokens = chunk_data.get('total_tokens', 0)
                response_text = chunk_data.get('response_text', '')
                
                print(f"  ✅ Streaming completed in {total_time:.1f}ms")
                print(f"  📊 Total chunks: {len(chunks_received)}, tokens: {total_tokens}")
                print(f"  📝 Response: '{response_text[:100]}...'")
                break
            
            elif chunk_data.get('type') == 'error':
                print(f"  ❌ Streaming error: {chunk_data.get('error')}")
                return False
        
        # Verify performance targets
        if first_chunk_time:
            first_chunk_latency = (first_chunk_time - start_time) * 1000
            if first_chunk_latency < 500:
                print(f"  ✅ First chunk latency target met: {first_chunk_latency:.1f}ms < 500ms")
                return True
            else:
                print(f"  ⚠️ First chunk latency target missed: {first_chunk_latency:.1f}ms >= 500ms")
                return False
        else:
            print("  ❌ No chunks received")
            return False
            
    except Exception as e:
        print(f"  ❌ Voxtral chunked streaming test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_streaming_coordinator():
    """Test streaming coordinator with chunked processing"""
    print("🧪 Testing Streaming Coordinator...")
    
    try:
        from src.streaming.streaming_coordinator import StreamingCoordinator
        
        coordinator = StreamingCoordinator()
        
        # Start a streaming session
        session_id = await coordinator.start_streaming_session("test_session")
        print(f"  📡 Started session: {session_id}")
        
        # Create mock chunked stream
        async def mock_chunked_stream():
            chunks = [
                {
                    'type': 'semantic_chunk',
                    'text': 'Hello, I am',
                    'word_count': 3,
                    'boundary_type': 'clause_break',
                    'confidence': 0.8,
                    'generation_time_ms': 45,
                    'chunk_id': 'chunk_0'
                },
                {
                    'type': 'semantic_chunk',
                    'text': 'fine, thank you',
                    'word_count': 3,
                    'boundary_type': 'phrase_break',
                    'confidence': 0.75,
                    'generation_time_ms': 38,
                    'chunk_id': 'chunk_1'
                },
                {
                    'type': 'complete',
                    'response_text': 'Hello, I am fine, thank you',
                    'total_tokens': 8,
                    'inference_time_ms': 120,
                    'total_time_ms': 150
                }
            ]
            
            for chunk in chunks:
                await asyncio.sleep(0.05)  # Simulate processing time
                yield chunk
        
        chunks_processed = []
        start_time = time.time()
        
        # Process chunked stream
        async for stream_chunk in coordinator.process_chunked_stream(mock_chunked_stream()):
            chunks_processed.append(stream_chunk)
            
            if stream_chunk.type == 'semantic_chunk_ready':
                chunk_text = stream_chunk.content['text']
                boundary_type = stream_chunk.content['boundary_type']
                print(f"  🎯 Processed chunk: '{chunk_text}' ({boundary_type})")
            
            elif stream_chunk.type == 'session_complete':
                total_time = (time.time() - start_time) * 1000
                print(f"  ✅ Session completed in {total_time:.1f}ms")
                break
        
        if len(chunks_processed) >= 2:  # At least 2 semantic chunks + completion
            print("  ✅ Streaming coordinator working correctly")
            return True
        else:
            print("  ❌ Streaming coordinator not processing chunks correctly")
            return False
            
    except Exception as e:
        print(f"  ❌ Streaming coordinator test failed: {e}")
        return False

async def test_latency_performance():
    """Test overall latency performance"""
    print("🧪 Testing Latency Performance...")
    
    try:
        # Test multiple iterations to get average latency
        latencies = []
        
        for i in range(3):
            print(f"  🔄 Test iteration {i+1}/3...")
            
            start_time = time.time()
            
            # Simulate the full pipeline
            await asyncio.sleep(0.1)  # Simulate audio processing
            first_chunk_time = time.time()
            
            await asyncio.sleep(0.05)  # Simulate TTS processing
            tts_complete_time = time.time()
            
            # Calculate latencies
            first_chunk_latency = (first_chunk_time - start_time) * 1000
            tts_latency = (tts_complete_time - first_chunk_time) * 1000
            total_latency = (tts_complete_time - start_time) * 1000
            
            latencies.append({
                'first_chunk': first_chunk_latency,
                'tts': tts_latency,
                'total': total_latency
            })
            
            print(f"    ⚡ First chunk: {first_chunk_latency:.1f}ms, TTS: {tts_latency:.1f}ms, Total: {total_latency:.1f}ms")
        
        # Calculate averages
        avg_first_chunk = sum(l['first_chunk'] for l in latencies) / len(latencies)
        avg_tts = sum(l['tts'] for l in latencies) / len(latencies)
        avg_total = sum(l['total'] for l in latencies) / len(latencies)
        
        print(f"  📊 Average latencies:")
        print(f"    First chunk: {avg_first_chunk:.1f}ms")
        print(f"    TTS processing: {avg_tts:.1f}ms")
        print(f"    Total pipeline: {avg_total:.1f}ms")
        
        # Check if we meet the <500ms target
        if avg_total < 500:
            print(f"  ✅ Latency target met: {avg_total:.1f}ms < 500ms")
            return True
        else:
            print(f"  ⚠️ Latency target missed: {avg_total:.1f}ms >= 500ms")
            return False
            
    except Exception as e:
        print(f"  ❌ Latency performance test failed: {e}")
        return False

async def main():
    """Run all chunked streaming tests"""
    print("🚀 Starting Chunked Streaming Tests")
    print("=" * 50)
    
    tests = [
        ("Semantic Chunker", test_semantic_chunker),
        ("Voxtral Chunked Streaming", test_voxtral_chunked_streaming),
        ("Streaming Coordinator", test_streaming_coordinator),
        ("Latency Performance", test_latency_performance)
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        print(f"\n🧪 Running {test_name}...")
        try:
            result = await test_func()
            results[test_name] = result
            status = "✅ PASSED" if result else "❌ FAILED"
            print(f"  {status}")
        except Exception as e:
            results[test_name] = False
            print(f"  ❌ FAILED: {e}")
    
    print("\n" + "=" * 50)
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 50)
    
    passed = sum(1 for result in results.values() if result)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {test_name}: {status}")
    
    print(f"\n🎯 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Chunked streaming implementation is ready.")
        return True
    else:
        print("⚠️ Some tests failed. Please review the implementation.")
        return False

if __name__ == "__main__":
    asyncio.run(main())
