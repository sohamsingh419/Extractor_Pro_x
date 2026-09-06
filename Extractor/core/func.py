import asyncio
from datetime import datetime
from pyrogram.errors import UserNotParticipant
from pyrogram.types import *
from config import CHANNEL_ID2
from Extractor.core import script
from Extractor.core.mongo.plans_db import premium_users


async def chk_user(query, user_id):
    user = await premium_users()
    if user_id in user:
        await query.answer("Premium User!!")
        return 0
    else:
        await query.answer("Sir, you don't have premium access!!", show_alert=True)
        return 1


async def get_seconds(time_string):
    def extract_value_and_unit(ts):
        value = ""
        unit = ""

        index = 0
        while index < len(ts) and ts[index].isdigit():
            value += ts[index]
            index += 1

        unit = ts[index:].lstrip()

        if value:
            value = int(value)

        return value, unit

    value, unit = extract_value_and_unit(time_string.lower())

    if unit == 's':
        return value
    elif unit == 'min':
        return value * 60
    elif unit == 'hour':
        return value * 3600
    elif unit == 'day':
        return value * 86400
    elif unit == 'month':
        return value * 86400 * 30
    elif unit == 'year':
        return value * 86400 * 365
    else:
        return 0


async def subscribe(app, message):
    # Force-join was removed: this bot must not redirect users to an
    # unrelated channel. Kept as a compatibility stub for old imports.
    return 0
