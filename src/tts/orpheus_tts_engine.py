"""
Orpheus TTS Engine - HTTP Server Integration
Connects to Orpheus-FastAPI server for high-quality TTS generation
"""

import os
import sys
import json
import time
import asyncio
import logging
import httpx
import base64
import wave
import io
from typing import Dict, Any, Optional, List

from src.utils.config import config

# Setup logging
tts_logger = logging.getLogger("orpheus_tts")
tts_logger.setLevel(logging.INFO)

class OrpheusTTSEngine:
    """
    Orpheus TTS Engine - HTTP Server Integration
    Connects to Orpheus-FastAPI server for TTS generation
    """
    
    def __init__(self):
        self.is_initialized = False
        self.sample_rate = 24000
        
        # Orpheus-FastAPI server configuration
        self.orpheus_server_url = f"http://{config.tts.orpheus_server.host}:{config.tts.orpheus_server.port}"
        self.timeout = config.tts.orpheus_server.timeout
        
        # Voice configuration - focusing on ऋतिका as requested
        self.available_voices = [
            "ऋतिका",  # Hindi - Primary voice as requested
            "tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe",  # English
            "pierre", "amelie", "marie",  # French
            "jana", "thomas", "max",  # German
            "유나", "준서",  # Korean
            "长乐", "白芷",  # Mandarin
            "javi", "sergio", "maria",  # Spanish
            "pietro", "giulia", "carlo"  # Italian
        ]
        self.default_voice = "ऋतिका"  # Set as default as requested
        
        tts_logger.info(f"OrpheusTTSEngine initialized for HTTP server integration")
        tts_logger.info(f"🎯 Default voice: {self.default_voice}")
        tts_logger.info(f"🌐 Server URL: {self.orpheus_server_url}")
    
    async def initialize(self):
        """Initialize the Orpheus TTS Engine by checking server connectivity"""
        try:
            tts_logger.info("🚀 Initializing Orpheus TTS Engine...")
            start_time = time.time()
            
            # Test server connectivity
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                try:
                    response = await client.get(f"{self.orpheus_server_url}/v1/models")
                    if response.status_code == 200:
                        tts_logger.info("✅ Orpheus-FastAPI server is accessible")
                        models = response.json()
                        tts_logger.info(f"📋 Available models: {len(models.get('data', []))}")
                        self.is_initialized = True
                    else:
                        tts_logger.error(f"❌ Orpheus server returned status {response.status_code}")
                        self.is_initialized = False
                        return
                except httpx.ConnectError:
                    tts_logger.error(f"❌ Cannot connect to Orpheus server at {self.orpheus_server_url}")
                    tts_logger.error("💡 Make sure Orpheus-FastAPI server is running on port 1234")
                    self.is_initialized = False
                    return
            
            init_time = time.time() - start_time
            tts_logger.info(f"🎉 Orpheus TTS Engine initialized in {init_time:.2f}s")
            
        except Exception as e:
            tts_logger.error(f"❌ Failed to initialize Orpheus TTS Engine: {e}")
            self.is_initialized = False
    
    async def _generate_with_orpheus_server(self, text: str, voice: str) -> Optional[bytes]:
        """
        Generate audio using Orpheus-FastAPI server
        """
        try:
            tts_logger.info(f"🌐 Sending request to Orpheus-FastAPI server for voice '{voice}'")
            
            # Prepare the request payload
            payload = {
                "model": "orpheus",
                "messages": [
                    {
                        "role": "user",
                        "content": text
                    }
                ],
                "voice": voice,
                "response_format": "wav",
                "stream": False
            }
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.orpheus_server_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    # Extract audio data from response
                    if "choices" in result and len(result["choices"]) > 0:
                        choice = result["choices"][0]
                        if "message" in choice and "audio" in choice["message"]:
                            audio_base64 = choice["message"]["audio"]
                            audio_data = base64.b64decode(audio_base64)
                            tts_logger.info(f"✅ Received audio from server: {len(audio_data)} bytes")
                            return audio_data
                        else:
                            tts_logger.error("❌ No audio data in server response")
                            return None
                    else:
                        tts_logger.error("❌ Invalid response format from server")
                        return None
                else:
                    tts_logger.error(f"❌ Server returned status {response.status_code}: {response.text}")
                    return None
                    
        except httpx.TimeoutException:
            tts_logger.error("❌ Request to Orpheus server timed out")
            return None
        except Exception as e:
            tts_logger.error(f"❌ Orpheus server request failed: {e}")
            return None
    

    
    def get_available_voices(self) -> List[str]:
        """Get list of available voices"""
        return self.available_voices.copy()
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get TTS model information"""
        return {
            "engine": "Orpheus-FastAPI",
            "server_url": self.orpheus_server_url,
            "sample_rate": self.sample_rate,
            "available_voices": len(self.available_voices),
            "default_voice": self.default_voice,
            "initialized": self.is_initialized
        }
    
    async def close(self):
        """Cleanup resources"""
        tts_logger.info("🧹 Orpheus TTS Engine resources cleaned up")
    
    async def generate_audio(self, text: str, voice: str = None) -> Optional[bytes]:
        """
        Generate audio from text using Orpheus-FastAPI server
        """
        voice = voice or self.default_voice
        tts_logger.info(f"🎵 Generating audio for text: '{text[:50]}...' with voice '{voice}'")
        
        if not self.is_initialized:
            tts_logger.error("❌ Orpheus TTS Engine not initialized - cannot generate audio")
            return None
        
        try:
            # Generate audio using Orpheus-FastAPI server
            audio_data = await self._generate_with_orpheus_server(text, voice)
            if audio_data:
                tts_logger.info(f"✅ Audio generated with Orpheus-FastAPI ({len(audio_data)} bytes)")
                return audio_data
            else:
                tts_logger.error("❌ Orpheus-FastAPI failed to generate audio")
                return None
                
        except Exception as e:
            tts_logger.error(f"❌ Error generating audio with Orpheus-FastAPI: {e}")
            return None
    

    

    

    

    

    

    
    def _get_language_for_voice(self, voice: str) -> str:
        """Get language code for voice"""
        voice_language_map = {
            "ऋतिका": "hi",  # Hindi
            "tara": "en", "leah": "en", "jess": "en", "leo": "en", 
            "dan": "en", "mia": "en", "zac": "en", "zoe": "en",  # English
            "pierre": "fr", "amelie": "fr", "marie": "fr",  # French
            "jana": "de", "thomas": "de", "max": "de",  # German
            "유나": "ko", "준서": "ko",  # Korean
            "长乐": "zh", "白芷": "zh",  # Mandarin
            "javi": "es", "sergio": "es", "maria": "es",  # Spanish
            "pietro": "it", "giulia": "it", "carlo": "it"  # Italian
        }
        return voice_language_map.get(voice, "en")





