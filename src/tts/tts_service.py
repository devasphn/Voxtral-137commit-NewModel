"""
TTS Service - High-level interface for text-to-speech functionality
Integrates with Voxtral system for seamless audio generation using Kokoro TTS
"""

import asyncio
import base64
import time
import logging
from typing import Dict, Any, Optional, List, AsyncGenerator
from io import BytesIO
import wave

from src.models.kokoro_model_realtime import KokoroTTSModel
from src.utils.config import config

# Setup logging
tts_service_logger = logging.getLogger("tts_service")
tts_service_logger.setLevel(logging.INFO)

def map_voice_to_kokoro(voice_name: str) -> str:
    """
    ✅ CRITICAL FIX: Map voice requests to appropriate Kokoro voices
    Default to English voice since Voxtral generates English responses
    """
    if voice_name in ["ऋतिका", "ritika"]:
        return "hm_omega"  # Hindi male voice
    elif voice_name in ["hindi", "हिंदी"]:
        return "hm_omega"  # Hindi voice
    # ✅ FIXED: Default to English female voice to match Voxtral output language
    return "af_heart"

class TTSService:
    """
    High-level TTS service for Voxtral integration
    Handles text-to-speech conversion with voice selection and audio streaming using Kokoro TTS
    """

    def __init__(self):
        self.kokoro_model = KokoroTTSModel()
        self.is_initialized = False

        # ✅ CRITICAL FIX: Configuration from config file - use English voice
        self.default_voice = "af_heart"  # ✅ FIXED: English voice (was hm_omega Hindi)
        self.sample_rate = config.tts.sample_rate
        self.enabled = config.tts.enabled

        # Performance tracking
        self.generation_stats = {
            "total_requests": 0,
            "total_audio_duration": 0.0,
            "total_processing_time": 0.0,
            "average_realtime_factor": 0.0
        }

        tts_service_logger.info("TTSService initialized with Kokoro TTS")
    
    async def initialize(self):
        """Initialize the TTS service and Kokoro engine"""
        try:
            tts_service_logger.info("🚀 Initializing TTS Service with Kokoro TTS...")
            if not success:
                raise Exception("Kokoro TTS model initialization failed")
            self.is_initialized = True
            tts_service_logger.info("✅ TTS Service initialized successfully")
        except Exception as e:
            tts_service_logger.error(f"❌ Failed to initialize TTS Service: {e}")
            # Don't raise the exception - allow the service to continue with degraded functionality
            tts_service_logger.warning("⚠️ TTS Service will continue with limited functionality")
            self.is_initialized = False
    
    async def generate_speech_async(self, text: str, voice: str = None, 
                                  return_format: str = "wav") -> Dict[str, Any]:
        """
        Generate speech from text asynchronously
        
        Args:
            text: Text to convert to speech
            voice: Voice to use (defaults to service default)
            return_format: Format for audio data ("wav", "raw", "base64")
            
        Returns:
            Dictionary with audio data and metadata
        """
        start_time = time.time()
        voice = voice or self.default_voice
        # Map voice to Kokoro voice format
        kokoro_voice = map_voice_to_kokoro(voice)

        tts_service_logger.info(f"🎵 Generating speech: '{text[:50]}...' with voice '{voice}' (mapped to Kokoro: '{kokoro_voice}')")
        
        # Try to initialize if not already done
        if not self.is_initialized:
            tts_service_logger.warning("⚠️ TTS Service not initialized, attempting initialization...")
            try:
                await self.initialize()
            except Exception as e:
                tts_service_logger.error(f"❌ Failed to initialize TTS during generation: {e}")
                return {
                    "success": False,
                    "error": "TTS Service initialization failed",
                    "audio_data": None,
                    "metadata": {}
                }
        
        try:
            # Generate speech using Kokoro TTS
            result = await self.kokoro_model.synthesize_speech(text, kokoro_voice)

            if not result.get("success", False):
                return {
                    "success": False,
                    "error": result.get("error", "Speech generation failed"),
                    "audio_data": None,
                    "metadata": {}
                }

            # Get audio data from Kokoro result
            audio_data = result["audio_data"]

            # Format audio data based on requested format
            formatted_audio = self._format_audio_data(audio_data, return_format)

            # Calculate performance metrics
            processing_time = time.time() - start_time
            # Estimate audio duration (Kokoro outputs at 24kHz)
            if hasattr(audio_data, '__len__'):
                audio_duration = len(audio_data) / (2 * 24000)  # 2 bytes per sample, 24kHz
            else:
                audio_duration = 0.0
            realtime_factor = audio_duration / processing_time if processing_time > 0 else 0

            # Update statistics
            self._update_stats(processing_time, audio_duration, realtime_factor)
            
            tts_service_logger.info(f"✅ Speech generated in {processing_time:.2f}s "
                                  f"({realtime_factor:.2f}x realtime)")
            
            return {
                "success": True,
                "audio_data": formatted_audio,
                "metadata": {
                    "voice": voice,
                    "kokoro_voice": kokoro_voice,
                    "text_length": len(text),
                    "audio_duration": audio_duration,
                    "processing_time": processing_time,
                    "realtime_factor": realtime_factor,
                    "sample_rate": 24000,  # Kokoro outputs at 24kHz
                    "format": return_format
                }
            }
            
        except Exception as e:
            tts_service_logger.error(f"❌ Error generating speech: {e}")
            return {
                "success": False,
                "error": str(e),
                "audio_data": None,
                "metadata": {}
            }

    async def generate_speech_streaming(self, text: str, voice: Optional[str] = None):
        """
        ULTRA-LOW LATENCY: Streaming speech generation
        Yields audio chunks as they are generated for immediate playback
        """
        if not self.is_initialized:
            tts_service_logger.warning("⚠️ TTS Service not initialized, attempting initialization...")
            await self.initialize()

        voice = voice or self.default_voice
        kokoro_voice = map_voice_to_kokoro(voice)

        try:
            tts_service_logger.info(f"🎵 Starting streaming generation: '{text[:50]}...' with voice '{voice}' (mapped to Kokoro: '{kokoro_voice}')")

            async for chunk_data in self.kokoro_model.synthesize_speech_streaming(text, kokoro_voice):
                if chunk_data.get('error'):
                    yield {
                        "success": False,
                        "error": chunk_data['error'],
                        "audio_chunk": None,
                        "is_final": True
                    }
                    return

                yield {
                    "success": True,
                    "audio_chunk": chunk_data.get('audio_chunk'),
                    "chunk_index": chunk_data.get('chunk_index', 0),
                    "is_final": chunk_data.get('is_final', False),
                    "voice": voice,
                    "kokoro_voice": kokoro_voice,
                    "sample_rate": 24000,  # Kokoro outputs at 24kHz
                    "synthesis_time_ms": chunk_data.get('synthesis_time_ms', 0)
                }

        except Exception as e:
            tts_service_logger.error(f"❌ Streaming generation failed: {e}")
            yield {
                "success": False,
                "error": str(e),
                "audio_chunk": None,
                "is_final": True
            }

    async def process_word_stream(self, word_stream: AsyncGenerator[str, None], voice: Optional[str] = None):
        """
        STREAMING VOICE AGENT: Process streaming words from Voxtral for immediate TTS
        Generates audio as words become available for ultra-low perceived latency
        """
        if not self.is_initialized:
            await self.initialize()

        voice = voice or self.default_voice
        kokoro_voice = map_voice_to_kokoro(voice)

        try:
            tts_service_logger.info(f"🎵 Starting word stream processing with voice '{voice}' (Kokoro: '{kokoro_voice}')")

            word_count = 0
            async for words_text in word_stream:
                if not words_text or not words_text.strip():
                    continue

                word_count += 1
                tts_service_logger.debug(f"🎵 Processing word chunk {word_count}: '{words_text}'")

                # Generate TTS for this word chunk
                async for chunk_data in self.kokoro_model.synthesize_speech_streaming(words_text, kokoro_voice):
                    if chunk_data.get('error'):
                        yield {
                            "success": False,
                            "error": chunk_data['error'],
                            "audio_chunk": None,
                            "is_final": True,
                            "word_chunk": word_count,
                            "source_text": words_text
                        }
                        return

                    yield {
                        "success": True,
                        "audio_chunk": chunk_data.get('audio_chunk'),
                        "chunk_index": chunk_data.get('chunk_index', 0),
                        "is_final": chunk_data.get('is_final', False),
                        "voice": voice,
                        "kokoro_voice": kokoro_voice,
                        "sample_rate": 24000,
                        "synthesis_time_ms": chunk_data.get('synthesis_time_ms', 0),
                        "word_chunk": word_count,
                        "source_text": words_text
                    }

            tts_service_logger.info(f"✅ Word stream processing completed: {word_count} word chunks processed")

        except Exception as e:
            tts_service_logger.error(f"❌ Word stream processing failed: {e}")
            yield {
                "success": False,
                "error": str(e),
                "audio_chunk": None,
                "is_final": True
            }

    def _format_audio_data(self, audio_data: Any, return_format: str) -> Any:
        """Format audio data according to requested format"""
        if return_format == "base64":
            if hasattr(audio_data, 'tobytes'):
                return base64.b64encode(audio_data.tobytes()).decode('utf-8')
            elif isinstance(audio_data, bytes):
                return base64.b64encode(audio_data).decode('utf-8')
            else:
                return base64.b64encode(str(audio_data).encode()).decode('utf-8')
        elif return_format == "raw":
            if hasattr(audio_data, 'tobytes'):
                return audio_data.tobytes()
            return audio_data
        else:  # wav format
            return audio_data
    
    def _update_stats(self, processing_time: float, audio_duration: float, realtime_factor: float):
        """Update performance statistics"""
        self.generation_stats["total_requests"] += 1
        self.generation_stats["total_audio_duration"] += audio_duration
        self.generation_stats["total_processing_time"] += processing_time
        
        # Calculate running average of realtime factor
        total_requests = self.generation_stats["total_requests"]
        current_avg = self.generation_stats["average_realtime_factor"]
        self.generation_stats["average_realtime_factor"] = (
            (current_avg * (total_requests - 1) + realtime_factor) / total_requests
        )
    
    def get_available_voices(self) -> List[str]:
        """Get list of available voices"""
        # Return Kokoro voices
        return ["af_heart", "af_bella", "af_nicole", "af_sarah", "hm_omega", "hf_alpha", "hf_beta", "hm_psi"]

    def get_service_info(self) -> Dict[str, Any]:
        """Get TTS service information and statistics"""
        engine_info = self.kokoro_model.get_model_info() if self.kokoro_model else {}

        return {
            "service": "TTSService",
            "engine": "Kokoro TTS",
            "initialized": self.is_initialized,
            "engine_info": engine_info,
            "statistics": self.generation_stats.copy(),
            "configuration": {
                "default_voice": self.default_voice,
                "sample_rate": 24000  # Kokoro sample rate
            }
        }
    
    async def stream_speech(self, text: str, voice: str = None) -> AsyncGenerator[bytes, None]:
        """
        Stream speech generation for real-time applications
        This would be used for streaming audio back to the client
        """
        if not self.is_initialized:
            raise RuntimeError("TTS Service not initialized")
        
        voice = voice or self.default_voice
        tts_service_logger.info(f"🎵 Streaming speech generation for voice '{voice}'")
        
        # This would implement streaming token processing
        # For now, yield empty as placeholder
        tts_service_logger.warning("⚠️ Speech streaming not yet implemented - placeholder")
        yield b""  # Placeholder
    
    def validate_voice(self, voice: str) -> bool:
        """Validate if a voice is available"""
        return voice in self.get_available_voices()
    
    def get_default_voice(self) -> str:
        """Get the default voice"""
        return self.default_voice
    
    def set_default_voice(self, voice: str) -> bool:
        """Set the default voice"""
        if self.validate_voice(voice):
            self.default_voice = voice
            tts_service_logger.info(f"Default voice changed to: {voice}")
            return True
        else:
            tts_service_logger.warning(f"Invalid voice: {voice}")
            return False

    async def generate_speech(self, text: str, voice: str = None) -> bytes:
        """
        Generate speech and return raw audio bytes (for compatibility with existing code)
        """
        result = await self.generate_speech_async(text, voice, return_format="raw")
        if result["success"]:
            return result["audio_data"]
        else:
            raise Exception(f"Speech generation failed: {result.get('error', 'Unknown error')}")

# Create a global TTS service instance
tts_service = TTSService()
