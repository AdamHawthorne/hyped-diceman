"""Server-specific Discord emoji IDs from the 29 October 2026 audit."""
import discord

EMOJI_IDS = {
    "Warrior": 1565476863835644200,
    "Paladin": 1565476577024807000,
    "Hunter": 1565476457667367000,
    "Rogue": 1565476692611563500,
    "Priest": 1565476633102651600,
    "Shaman": 1565476764283572500,
    "Mage": 1565476478957916400,
    "Warlock": 1565476809431191800,
    "Druid": 1565476364981633300,
    "Tank": 1565477012322132200,
    "Healer": 1565477112951996400,
    "Melee": 1565477048305197300,
    "Ranged": 1565477080228176000,
    "Caster": 1565477140429021200,
    "Dice": 1565477510651846700,
    "MoveTo": 1565477356326617000,
    "ogrecoin": 1564336079430099000
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
