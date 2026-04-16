# app/rerankers/reranker_llm.py
from typing import List, Dict, Any
import os, json
from dataclasses import dataclass

@dataclass
class LLMRerankerConfig:
    model: str = os.getenv("OPENAI_RERANK_MODEL", "gpt-4o-mini")
    max_passages: int = int(os.getenv("RERANK_MAX_PASSAGES", "40"))
    parallel: bool = True

class OpenAILLMReranker:
    def __init__(self, client, config: LLMRerankerConfig | None = None):
        self.client = client
        self.cfg = config or LLMRerankerConfig()

    def rerank(self, query: str, passages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        passages: list of {id, text, metadata}
        returns same list sorted by descending 'score'
        """
        if not passages:
            return []
        use = passages[: self.cfg.max_passages]

        # Build a compact scoring prompt
        def fmt_item(i, p):
            meta = p.get("metadata", {}) or {}
            tags = meta.get("domain_tags") or []
            sect = meta.get("breadcrumb", "") or ""
            title = meta.get("section_title", "") or ""
            
            # Show title/breadcrumb prominently
            header = f"[Title: {title}] [Section: {sect}]" if title or sect else f"[Section: {sect}]"
            tag_str = f"[Tags: {tags[:5]}]" if tags else ""  # Limit tags to first 5 to reduce noise
            
            # Show first 1200 chars (reduced from 1500 to fit more passages in context)
            return f"{i}. {header} {tag_str}\n{p.get('text','')[:1200]}"

        items = "\n\n".join(fmt_item(i+1, p) for i,p in enumerate(use))

        SYSTEM = """You are a maritime documentation expert. Your job is to score passages based on how well they answer the user's specific question.

🎯 CRITICAL SCORING PRINCIPLES:

1. **DOCUMENT TYPE AWARENESS** - Distinguish between:
   - PROCEDURAL/REQUIREMENT DOCUMENTS (lists what to do, what to maintain, procedures) ← PREFER for "what/which/when" questions
   - FORM TEMPLATES (CG-2692C, data collection forms with field descriptions) ← ONLY relevant if asking about the form itself
   - REFERENCE DOCUMENTS (cross-references, indices, summaries) ← Lower priority unless nothing else matches

2. **TITLE & SECTION HEADING = PRIMARY SIGNAL**:
   - If the title/heading DIRECTLY addresses the query → Score 9-10
   - Example: Query "records in NP 133C?" + Title "Recordkeeping - NP 133C" = 10/10
   - Example: Query "records in NP 133C?" + Title "Personnel Casualty Form CG-2692C" = 1-2/10 (wrong form!)

3. **CONTENT STRUCTURE MATTERS**:
   - Complete LISTS (bullets, numbered items) = 9-10/10
   - Step-by-step PROCEDURES = 9-10/10
   - Generic mentions without details = 4-6/10
   - Form field descriptions (Date of Birth, Date of Death, etc.) = Low score UNLESS query asks about form fields

4. **PRIMARY vs SECONDARY MENTIONS**:
   - Document's MAIN PURPOSE is the topic = 9-10/10
   - Document merely CROSS-REFERENCES the topic = 5-7/10
   - Document mentions topic in passing = 3-4/10

5. **SEMANTIC INTENT MATCHING**:
   - "enter in [DOCUMENT]" usually means "what to maintain in [DOCUMENT]" → Look for LISTS of items to maintain
   - "fill out [FORM]" means form instructions → Look for FIELD descriptions
   - "what records" = Looking for a LIST, not a form template
   - "how to" = Looking for PROCEDURES, not requirements

6. **CONTEXT VERIFICATION**:
   - Check if passage is from the RIGHT document/section
   - "NP 133C" in Passage Planning context ≠ "CG-2692C" in Incident Reporting
   - Form numbers matter: Don't confuse different forms!

7. **METADATA IS SUPPLEMENTARY**:
   - More VIQ codes ≠ Better match
   - More tags ≠ Better match
   - Focus on: Does this passage ANSWER the question?"""

        USER = f"""Query: {query}

🔍 SCORING TASK:
1. Read the query carefully to understand what the user is REALLY asking for
2. For each passage, check:
   - Is the title/section heading directly related?
   - Does the content type match (procedure vs form vs reference)?
   - Does it contain specific details or just mentions?
   - Is this the PRIMARY source or just a reference?

⚠️ SPECIAL ATTENTION:
- If query mentions a SPECIFIC FORM NUMBER (like NP 133C, CG-2692), verify the passage is about THAT exact form
- If query asks about "records to maintain/enter/keep", look for LISTS of recordkeeping requirements, NOT form field descriptions
- If you see form fields like "Date of Birth, Date of Death" → That's a FORM TEMPLATE, not recordkeeping requirements

📊 SCORING SCALE:
- 10: Perfect - Title matches + Complete list/procedure + Right document type
- 9: Excellent - Title matches + Good details + Right context
- 8: Very good - Highly relevant content + Right document type
- 7: Good - Relevant with some details
- 6: Okay - Mentions topic but incomplete or secondary mention
- 5: Marginal - Related but not quite what user needs
- 3-4: Weak - Tangentially related or wrong document type
- 1-2: Poor - Wrong context/form or irrelevant
- 0: Not relevant at all

Passages to score:
{items}

Return ONLY valid JSON array (no markdown blocks, no explanations):
[{{"idx": 1, "score": 10.0}}, {{"idx": 2, "score": 8.5}}, ...]

Sort by score descending (highest first)."""

        try:
            # Lazy import (user provides openai client compatible with responses.create)
            from openai import OpenAI
            api_key = os.getenv("OPENAI_API_KEY", "")
            client = self.client or OpenAI(api_key=api_key)

            resp = client.chat.completions.create(
                model=self.cfg.model,
                messages=[
                    {"role":"system","content":SYSTEM},
                    {"role":"user","content":USER},
                ],
                temperature=0.0,
                max_tokens=500,  # Limit response length for faster processing
            )
            txt = resp.choices[0].message.content.strip()
            
            # Handle markdown code blocks if present
            if txt.startswith("```"):
                lines = txt.split("\n")
                # Remove first line (```) and last line (```)
                txt = "\n".join(lines[1:-1])
                if txt.startswith("json"):
                    txt = txt[4:].strip()
            
            # Try to parse JSON
            data = json.loads(txt)
            
            # Validate data structure
            if not isinstance(data, list):
                raise ValueError("Response is not a JSON array")
            
            # Attach scores to passages
            scored_count = 0
            for it in data:
                idx = int(it.get("idx", 0)) - 1
                if 0 <= idx < len(use):
                    score = float(it.get("score", 0.0))
                    # Clamp score to valid range
                    score = max(0.0, min(10.0, score))
                    use[idx]["score"] = score
                    scored_count += 1
            
            # Sort by score descending
            use.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            
            # Logging for debugging
            try:
                from loguru import logger
                logger.info(f"[RERANK] Successfully scored {scored_count}/{len(use)} passages")
                logger.info(f"[RERANK] Top 3 scores after reranking:")
                for i, p in enumerate(use[:3], 1):
                    meta = p.get("metadata", {})
                    title = meta.get("section_title", "Unknown")
                    breadcrumb = meta.get("breadcrumb", "")
                    score = p.get("score", 0)
                    logger.info(f"  {i}. [Score: {score:.1f}] {title[:50]}")
                    if breadcrumb:
                        logger.info(f"      From: {breadcrumb[:60]}")
            except:
                pass  # Logging is optional
            
            # Return reranked passages + any that exceeded max_passages
            return use + passages[self.cfg.max_passages:]
            
        except json.JSONDecodeError as e:
            # JSON parsing failed
            try:
                from loguru import logger
                logger.error(f"[RERANK] JSON parsing failed: {e}")
                logger.error(f"[RERANK] Raw response: {txt[:200]}")
            except:
                pass
            return passages
            
        except Exception as e:
            # General error handling
            try:
                from loguru import logger
                logger.error(f"[RERANK] LLM reranking failed: {e}")
                logger.error(f"[RERANK] Query was: {query[:100]}")
            except:
                pass
            # Fallback: return original order if LLM fails
            return passages