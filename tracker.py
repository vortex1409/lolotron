import asyncio
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime,timedelta
import discord
from discord.ext import commands
from enum import Enum, auto
import json
from typing import Any,Dict,List,Tuple

'''
A convenience container for unpacking a rawReactionPayload from IDs to discord objects
'''
_rawReactionPayload = namedtuple('_rawReactionPayload', ['user', 'guild', 'channel', 'message', 'emoji'])

'''
An entry in the tracker. These are essentially timestamped reacts. Since discord
does not actually do any real accounting for these, we allow for tracking creation
and removal via the valid field.
'''
@dataclass
class trackerEntry:
    user:       discord.Member
    react:      discord.Emoji or str
    timeStamp:  datetime
    valid:      bool

    @staticmethod
    def encode(data) -> Dict[str, Any]:
        rtnData = {}

        rtnData['user'] = data.user.id

        if isinstance(data.react, str):
            rtnData['reactType'] = 'unicode'
            rtnData['react'] = data.react
        elif data.react.id is None:
            rtnData['reactType'] = 'unicode'
            rtnData['react'] = data.react.name
        else:
            rtnData['reactType'] = 'emoji'
            rtnData['react'] = data.react.id

        rtnData['timeStamp'] = data.timeStamp.timestamp()
        rtnData['valid']     = data.valid

        return rtnData

    @classmethod
    def decode(cls, client:commands.Bot, data:Dict[str, Any]):
        user  = client.get_user(data['user'])

        if data['reactType'] == 'unicode':
            react = data['react']
        elif data['reactType'] == 'emoji':
            react = client.get_emoji(data['react'])
            if react is None:
                print('Couldnt find emoji with ID {}'.format(data['react']))
        else:
            react = None

        timeStamp = datetime.fromtimestamp(data['timeStamp'])
        valid = data['valid']

        return trackerEntry(user, react, timeStamp, valid)

'''
A tracked item. This is essentially the message the bot will create, and a list of
reactions to it via the above trackerEntry objects.

Fields are as follows:
owner     - The discord user who created this request
message   - The user's message, consumer dependent on how to use this
msgObj    - The discordPy Message object for the message that is being tracked
entries   - A list of trackerEntry which are reactions
expire    - A datetime in UTC for when we will stop tracking this item
cogData   - Cog defined data
cogOwner  - The name of the registered Cog to lookup the function for callback
'''
@dataclass
class Tracker:
    owner:      discord.Member
    message:    str
    msgObj:     discord.Message
    entries:    List[trackerEntry]
    expire:     datetime
    cogData:    Any
    cogOwner:   str

    @staticmethod
    def encode(data) -> Dict[str, Any]:
        rtnData = {}

        rtnData['owner'] = data.owner.id
        rtnData['ownerGuild'] = data.owner.guild.id
        rtnData['msg']   = data.message
        rtnData['msgId'] = data.msgObj.id

        rtnData['entries'] = []
        for e in data.entries:
            rtnData['entries'].append(trackerEntry.encode(e))

        rtnData['expire'] = data.expire.timestamp()
        rtnData['cogOwner'] = data.cogOwner

        return rtnData

    @classmethod
    async def decode(cls, client:commands.Bot, data:Dict[str, Any]):
        ownerGuild = await client.fetch_guild(data['ownerGuild'])
        owner = await ownerGuild.fetch_member(data['owner'])

        message = data['msg']

        # There is no easy way to lookup a message given an ID. So
        # instead we must search through all channels we can see, and
        # try to find if they contain the message
        for c in client.get_all_channels():
            for tc in c.text_channels:
                print(tc)
                try:
                    msgObj = await tc.fetch_message(data['msgId'])
                except:
                    continue
                else:
                    break
            else:
                continue

            break
        else:
            msgObj = None

        entries = []
        for e in data['entries']:
            entries.append(trackerEntry.decode(client, e))

        expire = datetime.utcfromtimestamp(data['expire'])
        cogOwner = data['cogOwner']

        return Tracker(owner, message, msgObj, entries, expire, None, cogOwner)

'''
A Cog that tracks reactions to a message
'''
class reactTracker(commands.Cog):
    jsonFileName = 'reactTracker.json'

    def __init__(self, bot):
        self.bot = bot

        self.trackedItems = {}
        self.msgCb = {}
        self.procCb = {}

        bot.loop.create_task(self.load_settings())
        bot.loop.create_task(self.gc_task())

    '''
    Adds a lookup for a Cog to a callback function
    '''
    def registerCallbacks(self, name, msgCb, procCb):
        self.msgCb[name]  = msgCb
        self.procCb[name] = procCb

        print('Message Callback table is now:')
        print(self.msgCb)

        print('Process Callback table is now:')
        print(self.procCb)

    '''
    Creates a tracked object
    '''
    def createTrackedItem(self, msgObj:discord.Message, user:discord.user,
                          msg:str='', cogOwner=None, usrdata=None, expire:datetime=None):

        if expire is None:
            expireTime = datetime.utcnow() + timedelta(days=1, hours=12)
        else:
            expireTime = expire

        t = Tracker(user, msg, msgObj, [], expireTime, usrdata, cogOwner)

        self.trackedItems[msgObj.id] = t
        return t

    '''
    An accessor function to get the tracked object or return None if it
    doesn't exist
    '''
    def getTrackedItem(self, msgId) -> Tracker:
        if msgId not in self.trackedItems:
            return None
        else:
            return self.trackedItems[msgId]

    '''
    An accessor function to safely delete a tracked item before it expires
    This removes the item from being tracked, but does nothing to the message
    itself
    '''
    def deleteTrackedItem(self, msgId):
        try:
            self.trackedItems.pop(msgId)
        except:
            return

    '''
    A scheduled task to load previously saved setttings. This must be its own
    function rather than being done at startup becuase we need to do some
    async lookups from the server, which cannot be dont in _init_
    '''
    async def load_settings(self):
        # Need to wait until we're actually connected so we can do some of the lookups
        await self.bot.wait_until_ready()

        try:
            with open(self.jsonFileName, 'r') as f:
                jsonData = json.loads(f.read())

            for k,v in jsonData.items():
                t = await Tracker.decode(self.bot, v)
                self.trackedItems[int(k)] = t

        except Exception as e:
            print('loading exception')
            print(e)
            pass

        # Call registered process handlers for all the items now
        for k,v in self.trackedItems.items():
            if (v.cogOwner is not None) and (v.cogOwner in self.procCb):
                self.procCb[v.cogOwner](v)

        # Debug info
        print(self.trackedItems)

    '''
    A task that periodically runs the garbage collector
    '''
    async def gc_task(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            print('Running GC from automated task')
            await self.gc()
            # Sleep until time to run again
            await asyncio.sleep(120)

    '''
    A garbage collection function. This mainly cleans up the tracked list of expired
    events
    '''
    async def gc(self):
        expiredList = []
        cTime = datetime.utcnow()

        # Find all the tracking items that are expired
        for k,v in self.trackedItems.items():
            if v.expire <= cTime:
                print('GC found an expired event with id {}'.format(k))
                expiredList.append(k)

        # Remove all the expired events
        for k in expiredList:
            self.trackedItems.pop(k)

    '''
    Converts a rawReactionActionEvent payload to objects
    '''
    async def _unpackRawReaction(self, payload:discord.RawReactionActionEvent) -> _rawReactionPayload:

        channel = self.bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        emoji   = payload.emoji
        guild   = await self.bot.fetch_guild(payload.guild_id)
        print('Got guild: {}'.format(guild))

        # Try to look up the user in the guild to try to get the member
        # but if it fails we'll need to fallback to using a standard user lookup
        user = await guild.fetch_member(payload.user_id)
        print('Fetched member: {}'.format(user))
        if user is None:
            user = self.bot.get_user(payload.user_id)
        print('Fetched user: {}'.format(user))

        return _rawReactionPayload(user, guild, channel, message, emoji)

    '''
    Adds the user to the list of tracked events
    '''
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        # Unpack the parameters
        uPayload = await self._unpackRawReaction(payload)
        message  = uPayload.message
        emoji    = uPayload.emoji
        user     = uPayload.user

        # Ignore ourselves
        if user == self.bot.user:
            print('I ignored outselves')
            return

        # Run garbage collection so that we don't process expired events
        await self.gc()

        # Grab the message ID to see if we should even try to parse stuff
        msgId = message.id

        # Skip modifying anything if we aren't tracking on this message
        if msgId not in self.trackedItems:
            print('could not find {:d} in the tracker so ignoring this'.format(msgId))
            return
        else:
            event = self.trackedItems[msgId]

        # Check if the user is already in the list, this should really just be an edge case for the owner
        for e in event.entries:
            if (e.user == user) and (e.react == emoji) and (e.valid):
                print('exiting earlier')
                return

        # Add RSVP to the list
        newEntry = trackerEntry(user, emoji, datetime.utcnow(), True)
        event.entries.append(newEntry)

        #debug
        print(event)

        if (event.cogOwner is not None) and (event.cogOwner in self.msgCb):
            print('Modifying event')
            await self.msgCb[event.cogOwner](event)

    '''
    Removes the user from the list of tracked events
    '''
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        # Unpack the parameters
        uPayload = await self._unpackRawReaction(payload)
        message  = uPayload.message
        emoji    = uPayload.emoji
        user     = uPayload.user

        # Grab the message ID to see if we should even try to parse stuff
        msgId = message.id

        # Run garbage collection so that we don't process expired events
        await self.gc()

        # Skip modifying anything if we aren't tracking on this message
        if msgId not in self.trackedItems:
            print('could not find {:d} in the tracker so ignoring this'.format(msgId))
            return
        else:
            event = self.trackedItems[msgId]

        # Look for the user in the list. Since we are tracking all reacts, we need to
        # compare that it's the same user, emoji, and is the currently active one
        for e in event.entries:
            if (e.user == user) and (e.valid) and (emoji == e.react):
                rsvp = e
                break
        else:
            # Something goofy happened...so we'll just pretend it never happened
            print('reaction_remove sub-routine failed to find the user who un-reacted.')
            return

        # For auditing's sake, we don't delete entries, only invalidate them
        rsvp.valid = False

        # Debug
        print(event)

        # Modify the message
        if (event.cogOwner is not None) and (event.cogOwner in self.msgCb):
            print('Modifying event')
            await self.msgCb[event.cogOwner](event)


    '''
    An unloading function when things shutdown nicely. We try to save any in flight
    states we may have so that coming back we will pick up right where we left off.
    '''
    def cog_unload(self):
        try:
            with open(self.jsonFileName, 'w+') as f:
                json.dump(self.trackedItems, f, default=Tracker.encode, indent=4)
        except Exception as e:
            print('got exception')
            print(e)
        print('reactTracker unloading')