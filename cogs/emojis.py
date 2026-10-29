"""Server-specific Discord emoji IDs from the 29 October 2026 audit."""
import discord

EMOJI_IDS = {
    "Warrior": 1565476863835644034,
    "Paladin": 1565476577024806995,
    "Hunter": 1565476457667366912,
    "Rogue": 1565476692611563550,
    "Priest": 1565476633102651563,
    "Shaman": 1565476764283572375,
    "Mage": 1565476478957916310,
    "Warlock": 1565476809431191692,
    "Druid": 1565476364981633196,
    "Tank": 1565477012322132099,
    "Healer": 1565477112951996456,
    "Melee": 1565477048305197207,
    "Ranged": 1565477080228175973,
    "Caster": 1565477140429021285,
    "Dice": 1565477510651846767,
    "MoveTo": 1565477356326617183,
    "ogrecoin": 1564336079430099035,
}

def markup(name):
    value = EMOJI_IDS.get(name)
    return f"<:{name}:{value}>" if value else ""

def option_emoji(name):
    value = EMOJI_IDS.get(name)
    return discord.PartialEmoji(name=name, id=value) if value else None

def combat_role(role, klass="", spec=""):
    """Visual role classification; never changes persisted role."""
    if role not in ("DPS", "Dps"):
        return role
    klass = (klass or "").strip().title()
    spec = (spec or "").strip().title()
    if klass == "Hunter":
        return "Ranged"
    if klass in {"Mage", "Warlock"} or (klass, spec) in {
        ("Priest", "Shadow"), ("Druid", "Balance"), ("Shaman", "Elemental")
    }:
        return "Caster"
    return "Melee"
