import json
import logging
import itertools
from typing import Any, Dict, List, Set
from rapidfuzz import process, fuzz
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path

from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("red.holo-cogs.gptmemory")


class BooruTagsFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="search_booru_tags",
            description="Searches booru tags and tag groups. Tag groups may include many types of clothes like hat or legwear, as well as gestures, actions, expressions, locations, styles, body parts, animals, positions, composition, etc.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "A short term to search for matches among booru tags and tag groups.",
                    }},
                required=["query"],
            )))

    tag_groups: dict = {}
    all_tags: list = []

    @classmethod
    def normalize(cls, tag: str) -> str:
        tag = tag.lower()
        if len(tag) > 3:
            tag = tag.replace("_", " ")
        return tag
    
    @classmethod
    def build_index(cls, data: Dict[str, Any]):
        cls.tag_groups = {}
        for group_name, group_content in data.items():
            for subgroup_name, subgroup_content in group_content.items():
                if isinstance(subgroup_content, dict):
                    vals = [v if isinstance(v, (list, tuple)) else [v]
                            for v in subgroup_content.values()]
                    merged = list(itertools.chain.from_iterable(vals))
                    cls.tag_groups[cls.normalize(subgroup_name)] = [cls.normalize(t) for t in merged if t is not None]
                elif isinstance(subgroup_content, list):
                    cls.tag_groups[cls.normalize(subgroup_name)] = [cls.normalize(tag) for tag in subgroup_content]
        cls.all_tags = list(itertools.chain.from_iterable(cls.tag_groups.values()))                
        
    @classmethod
    def search_booru_tags(cls, query: str, fuzzy_threshold: int = 80) -> List[str]:
        query = cls.normalize(query)

        matches: Set[str] = set()

        for group, tags in cls.tag_groups.items():
            if query in group:
                matches.update(tags)
        
        fuzzy = process.extract(query, cls.all_tags, scorer=fuzz.WRatio, score_cutoff=fuzzy_threshold, limit=None)
        for tag, _, _ in fuzzy:
            matches.add(tag)

        return sorted(matches)

    async def run(self, arguments: dict) -> str:
        query = arguments["query"]

        if not self.tag_groups:
            bot: Red = self.ctx.bot
            cog: commands.Cog = bot.get_cog("GptMemory") # type: ignore
            with open(bundled_data_path(cog).absolute() / "tag_groups.json", "r") as fp:
                data = json.load(fp)
            self.build_index(data)

        results = self.search_booru_tags(query)
        if results:
            return ", ".join(results)
        else:
            return "(No results)"
