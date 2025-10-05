"""
Semantic Chunking Utility for Ultra-Low Latency Streaming
Detects meaningful boundaries in token streams for optimal TTS processing
"""

import re
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

# Setup logging
chunk_logger = logging.getLogger("semantic_chunking")
chunk_logger.setLevel(logging.INFO)

def preprocess_text_for_tts(text: str) -> str:
    """
    Preprocess text to remove markdown formatting and problematic characters
    that cause TTS delays or errors.

    Args:
        text: Raw text that may contain markdown formatting

    Returns:
        Cleaned text suitable for TTS synthesis
    """
    if not text:
        return ""

    # Strip leading/trailing whitespace
    text = text.strip()

    # Remove markdown bold/italic formatting: **text** or *text* -> text
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # **bold** -> bold
    text = re.sub(r'\*([^*]+)\*', r'\1', text)      # *italic* -> italic

    # Remove markdown headers: ### Header -> Header
    text = re.sub(r'^#{1,6}\s+', '', text)

    # Remove markdown list markers at start: - item or * item -> item
    text = re.sub(r'^[-*]\s+', '', text)

    # Remove standalone dashes/hyphens that generate no audio
    text = re.sub(r'^\s*-\s*$', '', text)

    # Remove multiple colons (problematic for TTS): :: -> :
    text = re.sub(r':+', ':', text)

    # Remove trailing colons that cause issues: "History:" -> "History"
    # But keep colons in middle of sentences
    text = re.sub(r':\s*$', '', text)

    # Remove multiple spaces
    text = re.sub(r'\s+', ' ', text)

    # Final strip
    text = text.strip()

    return text

def is_valid_tts_text(text: str, min_length: int = 1) -> bool:
    """
    Validate if text is suitable for TTS synthesis.

    Args:
        text: Text to validate
        min_length: Minimum length requirement (default: 1 character)

    Returns:
        True if text is valid for TTS, False otherwise
    """
    if not text:
        return False

    # Strip whitespace for validation
    text = text.strip()

    # Check minimum length
    if len(text) < min_length:
        return False

    # Reject empty strings
    if not text:
        return False

    # Reject strings with only special characters
    if re.match(r'^[^a-zA-Z0-9]+$', text):
        return False

    # Reject standalone dashes/hyphens
    if text in ['-', '--', '---', '*', '**', '***']:
        return False

    # Reject strings with only whitespace
    if text.isspace():
        return False

    return True

class ChunkBoundaryType(Enum):
    """Types of semantic boundaries for chunking"""
    SENTENCE_END = "sentence_end"      # . ! ?
    CLAUSE_BREAK = "clause_break"      # , ; :
    PHRASE_BREAK = "phrase_break"      # natural phrase boundaries
    WORD_COUNT = "word_count"          # minimum word count reached
    FORCED = "forced"                  # maximum token limit reached
    END_OF_STREAM = "end_of_stream"    # final chunk at end of generation

@dataclass
class SemanticChunk:
    """Data structure for semantic chunks"""
    text: str
    tokens: List[int]
    word_count: int
    boundary_type: ChunkBoundaryType
    confidence: float
    chunk_id: str
    timestamp: float

class SemanticChunker:
    """
    Ultra-Low Latency Semantic Chunker
    Detects optimal boundaries for streaming TTS processing
    """
    
    def __init__(self):
        # ULTRA-LOW LATENCY configuration for <100ms chunking
        self.config = {
            'min_words_per_chunk': 1,      # Single word chunks for maximum speed
            'max_words_per_chunk': 2,      # Very small chunks for immediate TTS
            'min_tokens_per_chunk': 1,     # Single token minimum for instant streaming
            'max_tokens_per_chunk': 4,     # Small token limit for speed
            'chunk_overlap_words': 0,      # No overlap for speed
            'confidence_threshold': 0.3    # Very low threshold for aggressive chunking
        }
        
        # Semantic boundary patterns
        self.sentence_endings = {'.', '!', '?'}
        self.clause_breaks = {',', ';', ':'}
        self.phrase_indicators = {
            'and', 'but', 'or', 'so', 'then', 'now', 'well', 'also',
            'however', 'therefore', 'meanwhile', 'furthermore'
        }
        
        # Common phrase patterns for natural breaks
        self.phrase_patterns = [
            r'\b(hello|hi|hey)\b',
            r'\b(thank you|thanks)\b',
            r'\b(how are you|how\'s it going)\b',
            r'\b(I am|I\'m)\b',
            r'\b(you are|you\'re)\b',
            r'\b(what is|what\'s)\b',
            r'\b(that is|that\'s)\b',
            r'\b(it is|it\'s)\b',
        ]
        
        # Current state
        self.current_buffer = ""
        self.current_tokens = []
        self.chunk_counter = 0
    
    def detect_chunk_boundary(self, text: str, tokens: List[int]) -> Tuple[bool, ChunkBoundaryType, float]:
        """
        Detect if current text represents a good chunk boundary
        Returns: (is_boundary, boundary_type, confidence)
        """
        words = text.strip().split()
        word_count = len(words)
        token_count = len(tokens)
        
        # Force chunk if maximum limits reached
        if (word_count >= self.config['max_words_per_chunk'] or 
            token_count >= self.config['max_tokens_per_chunk']):
            return True, ChunkBoundaryType.FORCED, 1.0
        
        # Don't chunk if below minimum
        if (word_count < self.config['min_words_per_chunk'] or 
            token_count < self.config['min_tokens_per_chunk']):
            return False, None, 0.0
        
        # Check for sentence endings (highest priority)
        if any(char in text for char in self.sentence_endings):
            return True, ChunkBoundaryType.SENTENCE_END, 0.95
        
        # Check for clause breaks
        if any(char in text for char in self.clause_breaks):
            # Only break on clause if we have enough content
            if word_count >= 3:
                return True, ChunkBoundaryType.CLAUSE_BREAK, 0.8
        
        # Check for phrase patterns
        text_lower = text.lower()
        for pattern in self.phrase_patterns:
            if re.search(pattern, text_lower):
                if word_count >= 2:
                    return True, ChunkBoundaryType.PHRASE_BREAK, 0.75
        
        # Check for phrase indicator words
        last_words = words[-2:] if len(words) >= 2 else words
        for word in last_words:
            if word.lower() in self.phrase_indicators:
                if word_count >= 3:
                    return True, ChunkBoundaryType.PHRASE_BREAK, 0.7
        
        # ULTRA-AGGRESSIVE: chunk on single complete word for maximum speed
        if word_count >= 1:
            return True, ChunkBoundaryType.WORD_COUNT, 0.5

        # Even chunk on single meaningful token if it looks like a word
        if token_count >= 1 and len(text.strip()) >= 2:
            return True, ChunkBoundaryType.WORD_COUNT, 0.4

        return False, None, 0.0
    
    def add_token(self, token_text: str, token_id: int, timestamp: float) -> Optional[SemanticChunk]:
        """
        Add a token to the current buffer and check for chunk boundaries
        Returns a SemanticChunk if a boundary is detected, None otherwise
        """
        # Add token to current buffer
        self.current_buffer += token_text
        self.current_tokens.append(token_id)

        # Check for chunk boundary
        is_boundary, boundary_type, confidence = self.detect_chunk_boundary(
            self.current_buffer, self.current_tokens
        )

        if is_boundary and confidence >= self.config['confidence_threshold']:
            # ✅ PREPROCESSING: Clean text before creating chunk
            cleaned_text = preprocess_text_for_tts(self.current_buffer)

            # ✅ VALIDATION: Only create chunk if text is valid for TTS
            if is_valid_tts_text(cleaned_text):
                # Create chunk with cleaned text
                chunk = SemanticChunk(
                    text=cleaned_text,
                    tokens=self.current_tokens.copy(),
                    word_count=len(cleaned_text.split()),
                    boundary_type=boundary_type,
                    confidence=confidence,
                    chunk_id=f"chunk_{self.chunk_counter}",
                    timestamp=timestamp
                )

                chunk_logger.debug(f"🔄 Created chunk: '{chunk.text}' ({chunk.boundary_type.value}, conf: {confidence:.2f})")

                # Reset buffer for next chunk
                self.current_buffer = ""
                self.current_tokens = []
                self.chunk_counter += 1

                return chunk
            else:
                # ✅ SKIP INVALID TEXT: Log and reset buffer without creating chunk
                chunk_logger.debug(f"⏭️ Skipped invalid chunk: '{cleaned_text}' (empty/special chars only)")
                self.current_buffer = ""
                self.current_tokens = []
                # Don't increment counter for skipped chunks

        return None

    def process_token(self, token_text: str, timestamp: float) -> Optional[SemanticChunk]:
        """
        Process a single token and return a semantic chunk if boundary detected
        This is the method referenced in the streaming code
        """
        # Generate a dummy token_id for compatibility
        token_id = hash(token_text) % 50000  # Simple hash-based ID
        return self.add_token(token_text, token_id, timestamp)

    def get_word_count(self) -> int:
        """Get current word count in buffer"""
        return len(self.current_buffer.strip().split()) if self.current_buffer.strip() else 0

    def finalize_chunk(self, timestamp: float) -> Optional[SemanticChunk]:
        """
        Finalize any remaining content in the buffer as a chunk
        Used at the end of streaming to capture remaining text
        """
        if self.current_buffer.strip():
            # ✅ PREPROCESSING: Clean text before creating final chunk
            cleaned_text = preprocess_text_for_tts(self.current_buffer)

            # ✅ VALIDATION: Only create chunk if text is valid for TTS
            if is_valid_tts_text(cleaned_text):
                # Create final chunk with cleaned content
                chunk = SemanticChunk(
                    text=cleaned_text,
                    tokens=self.current_tokens.copy(),
                    word_count=len(cleaned_text.split()),
                    boundary_type=ChunkBoundaryType.END_OF_STREAM,
                    confidence=1.0,
                    chunk_id=f"final_chunk_{self.chunk_counter}",
                    timestamp=timestamp
                )

                chunk_logger.debug(f"🔄 Finalized chunk: '{chunk.text}' (END_OF_STREAM)")

                # Reset buffer
                self.current_buffer = ""
                self.current_tokens = []
                self.chunk_counter += 1

                return chunk
            else:
                # ✅ SKIP INVALID TEXT: Log and reset buffer
                chunk_logger.debug(f"⏭️ Skipped invalid final chunk: '{cleaned_text}' (empty/special chars only)")
                self.current_buffer = ""
                self.current_tokens = []

        return None

    def reset(self):
        """Reset chunker state for new generation"""
        self.current_buffer = ""
        self.current_tokens = []
        self.chunk_counter = 0
        chunk_logger.debug("🔄 Chunker state reset")
    
    def get_stats(self) -> Dict[str, any]:
        """Get chunking statistics"""
        return {
            'chunks_created': self.chunk_counter,
            'current_buffer_length': len(self.current_buffer),
            'current_token_count': len(self.current_tokens),
            'config': self.config
        }

# Global chunker instance
semantic_chunker = SemanticChunker()
