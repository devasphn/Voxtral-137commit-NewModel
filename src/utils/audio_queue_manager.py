"""
Audio Queue Manager for Sequential TTS Playback
Ensures no audio overlap and maintains voice consistency across streaming chunks
"""
import asyncio
import logging
import time
from collections import deque
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import base64
import struct
import io

# Setup logging
audio_queue_logger = logging.getLogger("audio_queue_manager")

def create_wav_header(sample_rate: int, num_channels: int, bits_per_sample: int, data_size: int) -> bytes:
    """
    Create a proper WAV file header

    Args:
        sample_rate: Audio sample rate (e.g., 24000)
        num_channels: Number of audio channels (1 for mono, 2 for stereo)
        bits_per_sample: Bits per sample (16 for int16)
        data_size: Size of audio data in bytes

    Returns:
        WAV header bytes (44 bytes)
    """
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8

    # WAV file header structure
    header = struct.pack('<4sI4s',  # RIFF chunk descriptor
                        b'RIFF',
                        36 + data_size,  # File size - 8
                        b'WAVE')

    # fmt sub-chunk
    header += struct.pack('<4sIHHIIHH',
                         b'fmt ',
                         16,  # Subchunk1Size (16 for PCM)
                         1,   # AudioFormat (1 for PCM)
                         num_channels,
                         sample_rate,
                         byte_rate,
                         block_align,
                         bits_per_sample)

    # data sub-chunk
    header += struct.pack('<4sI',
                         b'data',
                         data_size)

    return header

def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000, num_channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """
    Convert raw PCM audio data to WAV format with proper headers

    Args:
        pcm_data: Raw PCM audio bytes (int16 format)
        sample_rate: Audio sample rate (default: 24000 Hz)
        num_channels: Number of channels (default: 1 for mono)
        bits_per_sample: Bits per sample (default: 16)

    Returns:
        Complete WAV file bytes with headers
    """
    data_size = len(pcm_data)
    wav_header = create_wav_header(sample_rate, num_channels, bits_per_sample, data_size)
    return wav_header + pcm_data

@dataclass
class AudioChunk:
    """Audio chunk with metadata for sequential playback"""
    audio_data: bytes
    chunk_id: str
    voice: str
    sample_rate: int
    chunk_index: int
    timestamp: float
    text_source: str
    conversation_id: str
    chunk_size_bytes: int = 0
    
    def __post_init__(self):
        if self.chunk_size_bytes == 0:
            self.chunk_size_bytes = len(self.audio_data)

class AudioQueueManager:
    """
    Manages sequential audio playback for TTS chunks
    Ensures no overlap and maintains voice consistency
    
    This is a SERVER-SIDE queue manager that coordinates with CLIENT-SIDE playback.
    It ensures chunks are sent in order and tracks playback state.
    """
    
    def __init__(self):
        # Queue for each conversation (supports multiple concurrent conversations)
        self.conversation_queues: Dict[str, asyncio.Queue] = {}
        self.conversation_workers: Dict[str, asyncio.Task] = {}
        
        # Voice consistency tracking
        self.conversation_voices: Dict[str, str] = {}
        
        # Playback state
        self.is_playing: Dict[str, bool] = {}
        self.chunks_sent: Dict[str, int] = {}
        
        # Performance tracking
        self.queue_latency_ms: Dict[str, deque] = {}
        self.send_latency_ms: Dict[str, deque] = {}
        
        # Global lock for thread safety
        self.manager_lock = asyncio.Lock()
        
        audio_queue_logger.info("✅ AudioQueueManager initialized")
    
    async def start_conversation_queue(self, conversation_id: str, websocket) -> bool:
        """
        Start a new audio queue for a conversation
        Returns True if started successfully
        """
        async with self.manager_lock:
            if conversation_id in self.conversation_queues:
                audio_queue_logger.warning(f"⚠️ Queue already exists for {conversation_id}")
                return False
            
            # Create new queue
            self.conversation_queues[conversation_id] = asyncio.Queue()
            self.conversation_voices[conversation_id] = None
            self.is_playing[conversation_id] = False
            self.chunks_sent[conversation_id] = 0
            self.queue_latency_ms[conversation_id] = deque(maxlen=100)
            self.send_latency_ms[conversation_id] = deque(maxlen=100)
            
            # Start background worker
            worker = asyncio.create_task(
                self._playback_worker(conversation_id, websocket)
            )
            self.conversation_workers[conversation_id] = worker
            
            audio_queue_logger.info(f"🎵 Started audio queue for conversation {conversation_id}")
            return True
    
    async def enqueue_audio(self, audio_chunk: AudioChunk) -> bool:
        """
        Add audio chunk to queue for sequential playback
        Returns True if enqueued successfully
        """
        enqueue_start = time.time()
        conversation_id = audio_chunk.conversation_id
        
        # Check if queue exists
        if conversation_id not in self.conversation_queues:
            audio_queue_logger.error(f"❌ No queue exists for {conversation_id}")
            return False
        
        # Voice consistency check
        current_voice = self.conversation_voices.get(conversation_id)
        if current_voice and current_voice != audio_chunk.voice:
            audio_queue_logger.warning(
                f"⚠️ Voice change detected in {conversation_id}: {current_voice} → {audio_chunk.voice}"
            )
        
        self.conversation_voices[conversation_id] = audio_chunk.voice
        
        # Add to queue
        await self.conversation_queues[conversation_id].put(audio_chunk)
        
        enqueue_time = (time.time() - enqueue_start) * 1000
        self.queue_latency_ms[conversation_id].append(enqueue_time)
        
        queue_size = self.conversation_queues[conversation_id].qsize()
        audio_queue_logger.debug(
            f"🎵 Enqueued audio chunk {audio_chunk.chunk_index} for {conversation_id} "
            f"(queue size: {queue_size}, latency: {enqueue_time:.1f}ms)"
        )
        
        return True
    
    async def _playback_worker(self, conversation_id: str, websocket):
        """
        Background worker that processes audio queue sequentially
        Ensures no overlap between chunks by sending them one at a time
        """
        audio_queue_logger.info(f"🎵 Playback worker started for {conversation_id}")
        
        try:
            queue = self.conversation_queues[conversation_id]
            
            while True:
                # Get next chunk from queue (blocks until available)
                audio_chunk = await queue.get()
                
                # Check if this is a termination signal
                if audio_chunk is None:
                    audio_queue_logger.info(f"🛑 Playback worker terminating for {conversation_id}")
                    break
                
                # Send audio chunk to client
                send_start = time.time()

                try:
                    # ✅ CRITICAL FIX: Convert raw PCM to WAV format with proper headers
                    # The server sends raw int16 PCM data, but browsers need WAV format
                    wav_data = pcm_to_wav(
                        audio_chunk.audio_data,
                        sample_rate=audio_chunk.sample_rate,
                        num_channels=1,  # Mono audio
                        bits_per_sample=16  # int16 format
                    )

                    # Verify WAV format
                    if wav_data[:4] != b'RIFF' or wav_data[8:12] != b'WAVE':
                        audio_queue_logger.error(f"❌ Invalid WAV format created for {audio_chunk.chunk_id}")
                        continue

                    audio_queue_logger.debug(f"✅ Created WAV file: {len(wav_data)} bytes (PCM: {len(audio_chunk.audio_data)} bytes, header: 44 bytes)")

                    # Encode WAV to base64
                    audio_b64 = base64.b64encode(wav_data).decode('utf-8')

                    # Send to WebSocket
                    import json
                    await websocket.send_text(json.dumps({
                        "type": "sequential_audio",
                        "conversation_id": conversation_id,
                        "audio_data": audio_b64,
                        "sample_rate": audio_chunk.sample_rate,
                        "chunk_index": audio_chunk.chunk_index,
                        "voice": audio_chunk.voice,
                        "text_source": audio_chunk.text_source,
                        "chunk_id": audio_chunk.chunk_id,
                        "chunk_size_bytes": len(wav_data),  # WAV size, not PCM size
                        "timestamp": time.time(),
                        "queue_position": self.chunks_sent[conversation_id],
                        "format": "wav"  # Indicate WAV format
                    }))
                    
                    self.chunks_sent[conversation_id] += 1
                    send_time = (time.time() - send_start) * 1000
                    self.send_latency_ms[conversation_id].append(send_time)
                    
                    audio_queue_logger.debug(
                        f"🔊 Sent chunk {audio_chunk.chunk_index} for {conversation_id} "
                        f"(latency: {send_time:.1f}ms, total sent: {self.chunks_sent[conversation_id]})"
                    )
                    
                except Exception as send_error:
                    audio_queue_logger.error(f"❌ Failed to send audio chunk: {send_error}")
                
                # Mark task as done
                queue.task_done()
                
                # Small delay to prevent overwhelming the client
                await asyncio.sleep(0.005)
                
        except Exception as e:
            audio_queue_logger.error(f"❌ Playback worker error for {conversation_id}: {e}")
            import traceback
            traceback.print_exc()
        finally:
            audio_queue_logger.info(f"🛑 Playback worker stopped for {conversation_id}")
    
    async def interrupt_playback(self, conversation_id: str):
        """
        ✅ BARGE-IN FEATURE: Immediately interrupt audio playback
        This is called when user starts speaking during TTS playback
        """
        async with self.manager_lock:
            if conversation_id not in self.conversation_queues:
                audio_queue_logger.debug(f"⚠️ No queue exists for {conversation_id} to interrupt")
                return

            audio_queue_logger.info(f"🛑 INTERRUPTION: User barge-in detected for {conversation_id}")

            # Clear queue immediately
            queue = self.conversation_queues[conversation_id]
            cleared_count = 0
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                    cleared_count += 1
                except asyncio.QueueEmpty:
                    break

            audio_queue_logger.info(f"🗑️ Cleared {cleared_count} pending audio chunks from queue")

            # Send stop signal to client
            if conversation_id in self.conversation_websockets:
                websocket = self.conversation_websockets[conversation_id]
                try:
                    await websocket.send_text(json.dumps({
                        "type": "audio_interrupted",
                        "conversation_id": conversation_id,
                        "cleared_chunks": cleared_count,
                        "timestamp": time.time()
                    }))
                    audio_queue_logger.info(f"📤 Sent audio_interrupted signal to client")
                except Exception as e:
                    audio_queue_logger.error(f"❌ Failed to send interrupt signal: {e}")

            # Reset playing state
            self.is_playing[conversation_id] = False

            audio_queue_logger.info(f"✅ Playback interrupted successfully for {conversation_id}")

    async def stop_conversation_queue(self, conversation_id: str):
        """Stop playback and clear queue for a conversation"""
        async with self.manager_lock:
            if conversation_id not in self.conversation_queues:
                audio_queue_logger.warning(f"⚠️ No queue exists for {conversation_id}")
                return

            audio_queue_logger.info(f"🛑 Stopping queue for {conversation_id}")

            # Clear queue
            queue = self.conversation_queues[conversation_id]
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

            # Send termination signal
            await queue.put(None)

            # Wait for worker to finish
            if conversation_id in self.conversation_workers:
                worker = self.conversation_workers[conversation_id]
                try:
                    await asyncio.wait_for(worker, timeout=2.0)
                except asyncio.TimeoutError:
                    audio_queue_logger.warning(f"⚠️ Worker timeout for {conversation_id}")
                    worker.cancel()

            # Cleanup
            del self.conversation_queues[conversation_id]
            del self.conversation_voices[conversation_id]
            del self.is_playing[conversation_id]
            del self.chunks_sent[conversation_id]
            del self.queue_latency_ms[conversation_id]
            del self.send_latency_ms[conversation_id]
            if conversation_id in self.conversation_workers:
                del self.conversation_workers[conversation_id]

            audio_queue_logger.info(f"✅ Queue stopped and cleaned up for {conversation_id}")
    
    def get_stats(self, conversation_id: str) -> Dict[str, Any]:
        """Get queue statistics for a conversation"""
        if conversation_id not in self.conversation_queues:
            return {"error": "Queue not found"}
        
        queue_latencies = self.queue_latency_ms.get(conversation_id, deque())
        send_latencies = self.send_latency_ms.get(conversation_id, deque())
        
        return {
            "conversation_id": conversation_id,
            "queue_size": self.conversation_queues[conversation_id].qsize(),
            "chunks_sent": self.chunks_sent.get(conversation_id, 0),
            "current_voice": self.conversation_voices.get(conversation_id),
            "is_playing": self.is_playing.get(conversation_id, False),
            "avg_queue_latency_ms": sum(queue_latencies) / len(queue_latencies) if queue_latencies else 0,
            "avg_send_latency_ms": sum(send_latencies) / len(send_latencies) if send_latencies else 0,
            "max_queue_latency_ms": max(queue_latencies) if queue_latencies else 0,
            "max_send_latency_ms": max(send_latencies) if send_latencies else 0
        }
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics for all active conversations"""
        return {
            "active_conversations": len(self.conversation_queues),
            "conversations": {
                conv_id: self.get_stats(conv_id)
                for conv_id in self.conversation_queues.keys()
            }
        }

# Global instance
audio_queue_manager = AudioQueueManager()

