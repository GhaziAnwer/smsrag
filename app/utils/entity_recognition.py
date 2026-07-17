# app/utils/entity_recognition.py
"""
Maritime Entity Recognition
Extracts domain-specific entities from user queries
"""

import re
from typing import Dict, List
from loguru import logger


class MaritimeEntityRecognizer:
    """
    Recognizes maritime-specific entities in queries.
    Helps improve retrieval by identifying key domain concepts.
    """
    
    def __init__(self):
        # Maritime regulations
        self.regulations = {
            "ISM Code": [r'\bism\s*code\b', r'\bism\b'],
            "SOLAS": [r'\bsolas\b'],
            "MARPOL": [r'\bmarpol\b'],
            "STCW": [r'\bstcw\b'],
            "ISPS": [r'\bisps\b', r'\bisps\s*code\b'],
            "MLC": [r'\bmlc\b', r'\bmlc\s*2006\b'],
            "IMDG": [r'\bimdg\b', r'\bimdg\s*code\b']
        }
        
        # Equipment
        self.equipment = {
            "ECDIS": [r'\becdis\b'],
            "Radar": [r'\bradar\b'],
            "GPS": [r'\bgps\b', r'\bgnss\b'],
            "VHF": [r'\bvhf\b'],
            "AIS": [r'\bais\b'],
            "EPIRB": [r'\bepirb\b'],
            "SART": [r'\bsart\b'],
            "Lifeboat": [r'\blife\s*boat\b', r'\blifeboat\b'],
            "Life Raft": [r'\blife\s*raft\b']
        }
        
        # Personnel
        self.personnel = {
            "Master": [r'\bmaster\b', r'\bcaptain\b'],
            "Chief Officer": [r'\bchief\s*officer\b', r'\bchief\s*mate\b'],
            "Chief Engineer": [r'\bchief\s*engineer\b'],
            "Navigation Officer": [r'\bnavigation\s*officer\b'],
            "OOBW": [r'\boobw\b', r'\bofficer\s*of\s*the\s*watch\b']
        }
        
        # Procedures
        self.procedures = {
            "Fire Drill": [r'\bfire\s*drill\b'],
            "Boat Drill": [r'\bboat\s*drill\b'],
            "Emergency Response": [r'\bemergency\s*response\b'],
            "Passage Planning": [r'\bpassage\s*plan(?:ning)?\b'],
            "Risk Assessment": [r'\brisk\s*assessment\b']
        }
        
        # Forms pattern
        self.forms = {
            "pattern": r'\b(NP\s*\d{3,4}[A-Z]?|CG[-\s]?\d{4}[A-Z]?|[A-Z]{2,3}[-\s]?\d{2,4}[A-Z]?)\b'
        }
    
    def extract_entities(self, query: str) -> Dict[str, List[str]]:
        """
        Extract all maritime entities from the query.
        
        Returns:
            Dict with categories: regulations, equipment, personnel, procedures, forms
        """
        query_lower = query.lower()
        entities = {
            "regulations": [],
            "equipment": [],
            "personnel": [],
            "procedures": [],
            "forms": []
        }
        
        # Extract regulations
        for name, patterns in self.regulations.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    if name not in entities["regulations"]:
                        entities["regulations"].append(name)
                    break
        
        # Extract equipment
        for name, patterns in self.equipment.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    if name not in entities["equipment"]:
                        entities["equipment"].append(name)
                    break
        
        # Extract personnel
        for name, patterns in self.personnel.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    if name not in entities["personnel"]:
                        entities["personnel"].append(name)
                    break
        
        # Extract procedures
        for name, patterns in self.procedures.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    if name not in entities["procedures"]:
                        entities["procedures"].append(name)
                    break
        
        # Extract forms
        form_matches = re.findall(self.forms["pattern"], query.upper())
        if form_matches:
            entities["forms"] = list(set(form_matches))
        
        return entities
    
    def get_entity_summary(self, entities: Dict[str, List[str]]) -> str:
        """
        Create a human-readable summary of detected entities.
        
        Example: "regulations: SOLAS, ISM Code | equipment: ECDIS"
        """
        parts = []
        for category, items in entities.items():
            if items:
                items_str = ", ".join(items)
                parts.append(f"{category}: {items_str}")
        return " | ".join(parts) if parts else "No specific entities detected"
    
    def has_entities(self, entities: Dict[str, List[str]]) -> bool:
        """Check if any entities were found."""
        return any(items for items in entities.values())


# Singleton instance
_recognizer = None

def get_entity_recognizer() -> MaritimeEntityRecognizer:
    """Get or create entity recognizer singleton."""
    global _recognizer
    if _recognizer is None:
        _recognizer = MaritimeEntityRecognizer()
        logger.info("[ENTITY] Maritime entity recognizer initialized")
    return _recognizer