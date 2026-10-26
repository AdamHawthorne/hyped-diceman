"""Read-only dummy examples and display-only labels for the LFG preview.

These sample characters are never written to SQLite. Canonical stored time
values remain 24-hour UK time for accurate chronological sorting.
"""

import re

from .lfg_sort import listing_board_key


_TIME = re.compile(r"(?<!\d)([01]\d|2[0-3]):00 UK")


def display_uk_times(value):
    """Make 01:00 UK -> 1am UK and 13:00 UK -> 1pm UK."""
    def replace(match):
        hour = int(match.group(1))
        return f"{hour % 12 or 12}{'am' if hour < 12 else 'pm'} UK"

    return _TIME.sub(replace, str(value))


def display_race(race):
    """Shorten only the board label, never race/class validation values."""
    return "Skyborne" if race == "Skyborne (Windshaper)" else race


# Character, race, class, roles, start/finish, playing days, plan, notes.
# Intentionally varied to demonstrate sorted, solo, LFM and open status.
DEMO_PLAYERS = (
    ("Grim Ironjaw", "Orc", "Warrior", "Tank, DPS",
     "Launch 23:00 UK - 04:00 UK", "Launch Night · Wed 4 Nov, Thu 5 Nov",
     "Group Questing + Dungeons (LFM)", "RFC at launch"),
    ("Murka Swiftblade", "Troll", "Rogue", "DPS",
     "00:00 UK - 06:00 UK", "Thu 5 Nov",
     "Open / Flexible", ""),
    ("Ashenveil", "Undead", "Mage", "DPS",
     "02:00 UK - 12:00 UK", "Thu 5 Nov, Fri 6 Nov",
     "Solo Questing", "Taking it steady"),
    ("Boulderhoof", "Tauren", "Druid", "Tank, Healer",
     "04:00 UK - 22:00 UK", "Thu 5 Nov, Fri 6 Nov",
     "Group Questing (LFM)", "Booked time off"),
    ("Skyla Dawn", "Skyborne (Windshaper)", "Druid", "Healer, DPS",
     "06:00 UK - 18:00 UK", "Thu 5 Nov, Sat 7 Nov",
     "Open / Flexible", ""),
    ("Frostfang", "Orc", "Shaman", "Healer, DPS",
     "08:00 UK - 21:00 UK", "Thu 5 Nov, Fri 6 Nov",
     "Full Group / Sorted", "Group of five"),
    ("Rattlebone", "Undead", "Priest", "Healer, DPS",
     "10:00 UK - 20:00 UK", "Fri 6 Nov, Sat 7 Nov",
     "Group Questing + Dungeons (LFM)", "Need tank"),
    ("Duststep", "Tauren", "Hunter", "DPS",
     "01:00 UK + 24 hours", "Sat 7 Nov",
     "Solo Questing + Dungeons (LFG/ Pugging)", "Pugging dungeons"),
    ("Stormcaller", "Troll", "Warrior", "Tank, DPS",
     "12:00 UK - 23:00 UK", "Sat 7 Nov, Sun 8 Nov",
     "Grinding (Dungeons, grind spots etc)", ""),
    ("Latearrival", "Skyborne (Windshaper)", "Hunter", "DPS",
     "Later - 23:00 UK", "Sun 8 Nov",
     "Open / Flexible", "Casual"),
    ("Grombonk", "Orc", "Warrior", "Tank, DPS",
     "01:00 UK - 10:00 UK", "Thu 5 Nov, Fri 6 Nov",
     "Group Questing (LFM)", "Looking for healer"),
    ("Sableth", "Undead", "Warlock", "DPS",
     "03:00 UK - 13:00 UK", "Thu 5 Nov",
     "Open / Flexible", ""),
    ("Earthsong", "Tauren", "Shaman", "Healer, DPS",
     "05:00 UK - 17:00 UK", "Thu 5 Nov, Sat 7 Nov",
     "Group Questing + Dungeons (LFM)", "Bring snacks"),
    ("Arrowfall", "Troll", "Hunter", "DPS",
     "07:00 UK - 20:00 UK", "Thu 5 Nov",
     "Solo Questing", ""),
    ("Windborne", "Skyborne (Windshaper)", "Warrior", "Tank, DPS",
     "09:00 UK - 21:00 UK", "Thu 5 Nov",
     "Open / Flexible", "Dungeons welcome"),
    ("Duskwhisper", "Undead", "Priest", "Healer, DPS",
     "11:00 UK - 20:00 UK", "Fri 6 Nov",
     "Group Questing (LFM)", "Can heal"),
    ("Kragstep", "Orc", "Hunter", "DPS",
     "02:00 UK - 17:00 UK", "Fri 6 Nov",
     "Open / Flexible", ""),
    ("Thundertotem", "Tauren", "Druid", "Tank, Healer",
     "03:00 UK - 23:00 UK", "Sat 7 Nov",
     "Full Group / Sorted", "Ready with mates"),
    ("Hexgloom", "Troll", "Mage", "DPS",
     "06:00 UK - 18:00 UK", "Sat 7 Nov",
     "Group Questing + Dungeons (LFM)", "Quest + RDF"),
    ("Bonecrackle", "Undead", "Rogue", "DPS",
     "Later - 23:00 UK", "Sun 8 Nov",
     "Open / Flexible", "Working launch night"),
)


def demo_entries():
    """Ten believable, chronologically ordered examples; no database access."""
    result = []
    for index, (name, race, cls, roles, availability, days, plan, notes) in enumerate(DEMO_PLAYERS):
        zone = {
            "Orc": "Durotar",
            "Troll": "Durotar",
            "Tauren": "Mulgore",
            "Undead": "Tirisfal Glades",
            "Skyborne (Windshaper)": "Zephras Isle",
        }[race]
        result.append({
            "channel_id": 0,
            "user_id": index + 1,
            "character": name,
            "race": race,
            "class_name": cls,
            "role": roles,
            "availability": availability,
            "days_off": days,
            "activity": plan,
            "notes": notes,
            "starting_zone": zone,
            "avatar_url": f"https://cdn.discordapp.com/embed/avatars/{index % 6}.png",
        })
    return sorted(result, key=listing_board_key)
