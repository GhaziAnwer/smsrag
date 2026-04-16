"""
Intelligent Query Rewriter for Maritime RAG System
Rewrites ambiguous queries to improve retrieval accuracy
Works for ANY maritime safety management system documentation
"""
import os
import logging
from typing import Optional, Tuple
from openai import OpenAI

logger = logging.getLogger(__name__)


class QueryRewriter:
    """
    Rewrites ambiguous queries to be more specific and retrieval-friendly.
    Uses LLM to understand user intent and add missing context.
    Works for any maritime safety management documentation.
    """
    
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = os.getenv("QUERY_REWRITE_MODEL", "gpt-4o-mini")
    
    def needs_rewriting(self, query: str) -> bool:
        """
        Determine if a query is ambiguous and needs rewriting.
        
        Ambiguous queries typically:
        - Are very short (< 10 words)
        - Lack specific context
        - Use acronyms without explanation
        - Ask generic "what/which" questions without domain context
        """
        query_lower = query.lower().strip()
        word_count = len(query.split())
        
        # Very short queries likely need expansion
        if word_count < 6:
            return True
        
        # Short queries with generic question words
        generic_starters = ["what", "which", "how", "where", "when", "who", "list"]
        starts_generic = any(query_lower.startswith(word) for word in generic_starters)
        
        if starts_generic and word_count < 10:
            return True
        
        # Queries that are just acronyms or very terse
        if word_count < 4:
            return True
        
        return False
    
    def rewrite_query(self, original_query: str) -> Tuple[str, bool]:
        """
        Rewrite the query to be more specific and retrieval-friendly.
        
        Returns:
            (rewritten_query, was_rewritten)
        """
        # Skip rewriting if query is already detailed enough
        if not self.needs_rewriting(original_query):
            logger.info(f"[QUERY_REWRITE] Query is specific enough, no rewriting needed")
            return original_query, False
        
        try:
            system_prompt = """You are an expert at understanding maritime safety management system documentation queries.

Your task: Analyze user queries about ship operations, safety procedures, and maritime regulations, then rewrite them to be more specific for document retrieval.

CRITICAL RULES:
1. **Preserve original intent** - Don't change what the user is asking about
2. **Add maritime context** - If query mentions logbooks, records, procedures, or equipment, add relevant operational context
3. **Expand acronyms** - If you recognize common maritime acronyms, expand them (e.g., ECDIS, SMS, ISM, SOLAS)
4. **Add procedural context** - For "how to" questions, mention if it's about procedures, checklists, or requirements
5. **Specify record types** - If asking about "records" or "entries", try to infer what type (maintenance, operational, safety, navigation)
6. **Keep it natural** - Rewritten query should sound like a human wrote it
7. **Don't add unrelated info** - Only add context that's clearly implied by the original query
8. **Don't assume** - If query is already specific, return it as-is

Your goal: Help the retrieval system find the RIGHT documents by making implicit context explicit.

Examples of good rewrites:
- "Which records?" → "Which operational and safety records should be maintained?"
- "How to do inspection?" → "What are the procedures and checklists for conducting vessel inspections?"
- "ECDIS failure" → "What are the procedures for ECDIS system failure and backup navigation?"
- "What in logbook?" → "What entries and records should be maintained in the ship's logbook?"

Respond with ONLY the rewritten query, no explanation or quotes."""

            user_prompt = f"""Original query from ship crew: "{original_query}"

Analyze this query and rewrite it to be more specific for retrieving maritime safety management documentation. Add relevant context about procedures, operations, or record-keeping if the query is ambiguous."""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,  # Low temperature for consistent rewrites
                max_tokens=150
            )
            
            rewritten = response.choices[0].message.content.strip()
            
            # Clean up the response
            rewritten = rewritten.strip('"\'')
            
            # Sanity check: If rewritten is too different or too long, use original
            if len(rewritten) > len(original_query) * 3:
                logger.warning(f"[QUERY_REWRITE] Rewritten query too long, using original")
                return original_query, False
            
            if len(rewritten.split()) < 3:
                logger.warning(f"[QUERY_REWRITE] Rewritten query too short, using original")
                return original_query, False
            
            logger.info(f"[QUERY_REWRITE] ✨ Query enhanced for better retrieval")
            logger.info(f"[QUERY_REWRITE] Original: '{original_query}'")
            logger.info(f"[QUERY_REWRITE] Enhanced: '{rewritten}'")
            
            return rewritten, True
            
        except Exception as e:
            logger.error(f"[QUERY_REWRITE] Failed to rewrite query: {e}")
            # Always return original query if rewriting fails
            return original_query, False
    
    def rewrite_with_context(
        self, 
        original_query: str, 
        previous_query: Optional[str] = None,
        conversation_context: Optional[str] = None
    ) -> Tuple[str, bool]:
        """
        Advanced rewriting with conversation context.
        
        Args:
            original_query: Current user query
            previous_query: Previous query in conversation (if any)
            conversation_context: Summary of conversation so far
        
        Returns:
            (rewritten_query, was_rewritten)
        """
        # If we have conversation context, use it
        if previous_query or conversation_context:
            try:
                context_info = ""
                if previous_query:
                    context_info += f"\nPrevious query: {previous_query}"
                if conversation_context:
                    context_info += f"\nConversation context: {conversation_context}"
                
                system_prompt = """You are an expert at understanding maritime documentation queries in context.

Rewrite the current query considering the conversation history to make it more specific."""

                user_prompt = f"""Current query: "{original_query}"{context_info}

Rewrite the current query to be self-contained and specific, incorporating relevant context from the conversation."""

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.2,
                    max_tokens=150
                )
                
                rewritten = response.choices[0].message.content.strip().strip('"\'')
                logger.info(f"[QUERY_REWRITE] Context-aware rewrite: '{rewritten}'")
                return rewritten, True
                
            except Exception as e:
                logger.error(f"[QUERY_REWRITE] Context-aware rewrite failed: {e}")
                # Fall back to regular rewriting
                return self.rewrite_query(original_query)
        else:
            # No context, use regular rewriting
            return self.rewrite_query(original_query)


# Convenience function for easy integration
def rewrite_query_if_needed(query: str) -> Tuple[str, bool]:
    """
    Convenience function to rewrite a query if needed.
    
    Returns:
        (possibly_rewritten_query, was_rewritten)
    """
    rewriter = QueryRewriter()
    return rewriter.rewrite_query(query)