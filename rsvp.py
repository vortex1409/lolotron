# External Libraries
from datetime import datetime, timedelta
import discord
from discord.ext import commands
import emoji
import re
import textwrap
from typing import Any,Dict,List,Tuple

# Internal Libraries
import tracker

class rsvp(commands.Cog):
    """ Create event RSVPs.

    RSVPs allow for orderly tracking of sign-ups.
    """


    # Maybe one day we can set this per server in settings...but that requires a bit
    # more work than I'm willing to do right now.
    # Note: This **MUST** be a PartialEmoji or Emoji object otherwise all of the compares
    #       will fall apart
    # For HIDE:
    #rsvpEmoji = discord.PartialEmoji(animated=False, name='nomcookie', id=563107909828083742)
    # For Development:
    rsvpEmoji = discord.PartialEmoji(animated=False, name='tempest', id=556941054277058560)

    templateMessageHead = \
    '''
    Posted by: {}

    '''

    templateMessageBody = \
    '''

    Please react to this message with {} to join.
    Removing your reaction will lose your spot in the queue.

    Signup:
    '''

    # TODO: This specifies that time is always in UTC, which is currently true.
    #       But hopefully it won't always be that way
    templateMessageFoot = \
    '''
    ```
    SystemID: {}
    Expiration Time: {} UTC
    ```
    '''

    expireTimeIncr = timedelta(days=3, hours=0)
    expireTimeExt  = timedelta(days=1, hours=0)
    expireTimeFmt  = '%A %b %d - %H:%M:%S %Z'

    # RegEx to search a message for a line starting with a discord emoji
    emojiRegex = re.compile(r'(<:(\w*):(\d*)>)')

    # Regex to search a message for a line starting with a unicode emoji
    emojiList = map(lambda x: ''.join(x.split()), emoji.UNICODE_EMOJI.keys())
    unicodeEmojiRegex = re.compile('|'.join(re.escape(p) for p in emojiList))

    def __init__(self, bot):
        self.bot = bot

        self.rsvps = {}

        # Register out callbacks with the reactionTracker
        self.tracker = self.bot.get_cog('reactTracker')
        self.tracker.registerCallbacks(type(self).__name__, self.msgGenerator, self.parseMsg)

    '''
    Helper function that generates the RSVP message
    '''
    async def msgGenerator(self, event:tracker.Tracker):
        # Create the main message
        # This is broken up this way to prevent stupid tabs from making indents look weird
        msg  = textwrap.dedent(self.templateMessageHead.format(event.owner.display_name))
        msg += event.message
        msg += textwrap.dedent(self.templateMessageBody.format(self.rsvpEmoji))

        # First collect the list of valid signups as well as valid reacts to the special reacts
        # To prevent strange shenanigans, the owner is always first regardless if they have
        # the appropriate react or not
        signups = [event.owner]
        sreacts = {}
        for e in event.entries:
            # Ignore invalid entries
            if not e.valid:
                continue

            # Check if this is the signup react
            if e.react == self.rsvpEmoji:
                # Prevent double counting if the owner reacted againg
                if e.user in signups:
                    continue

                signups.append(e.user)
                continue

            # Check if this is a special react
            for r in event.cogData:
                if e.react == r:
                    if e.user in sreacts:
                        sreacts[e.user].append(r)
                    else:
                        sreacts[e.user] = [r]

        # Go through the signup list in order, adding special reacts if applicable
        # Signup list enumeration always starts at 1 for non-programmers
        cnt = 1
        for s in signups:
            msg += '{} - {}'.format(cnt, s.display_name)
            if s in sreacts:
                msg += ' [ '
                for r in sreacts[s]:
                    msg += '{} '.format(r)
                msg += ']'
            msg += '\n'
            cnt += 1

        # Append the footer information
        msg += textwrap.dedent(self.templateMessageFoot.format(event.msgObj.id, event.expire.strftime(self.expireTimeFmt)))

        await event.msgObj.edit(content = msg)

    '''
    Parsers a message for emojis that are at the start of the line, indicating that they
    are special
    '''
    def parseMsg(self, event:tracker.Tracker):
        trackedEmojis = []

        for s in iter(event.message.splitlines()):
            # Search if its a Discord style emoji first
            matchObj = self.emojiRegex.search(s)
            print(matchObj)
            if matchObj is not None:
                tEmoji = discord.PartialEmoji(animated=False, name=matchObj.group(2), id=int(matchObj.group(3)))
                trackedEmojis.append(tEmoji)
                continue

            # Next try to lookup the  by unicode
            #matchObj = self.unicodeEmojiRegex.search(s)
            matchObj = emoji.get_emoji_regexp().search(s)
            print(matchObj)
            if matchObj is not None:
                tEmoji = discord.PartialEmoji(animated=False, name=matchObj.group(0), id=None)
                trackedEmojis.append(tEmoji)
                continue

        event.cogData = trackedEmojis
        print(trackedEmojis)

    @commands.group(pass_context=True)
    async def rsvp(self, ctx):
        pass

    @rsvp.command(brief = '''Create a new RSVP event''',
                  help  = '''Create a new RSVP event. All text after the "add" command will be used in the message
                           as is. You can use any basic discord or serer hosted emoji in your text''',
                  usage = '''<msg>''')
    async def add(self, ctx, *, msgBody):
        # Get the owner from the context
        owner = ctx.author

        # We need to create a message and send it to get a messageID, since we use the messageID as the identifier
        # We will edit the actual content later
        msg = await ctx.channel.send('Preparing an RSVP message...')

        # Finish setting up the RSVP Event Object
        t = self.tracker.createTrackedItem(msg, owner, msg=msgBody, cogOwner=type(self).__name__)

        # Search for special emojis
        self.parseMsg(t)

        # Update the RSVP Message from the bot
        await self.msgGenerator(t)

        # For convenience, add the reaction to the post so people don't have to dig it up
        await t.msgObj.add_reaction(self.rsvpEmoji)

        for e in t.cogData:
            await t.msgObj.add_reaction(e)

        # Delete the original message now that we're done parsing it
        await ctx.message.delete()

    @rsvp.command(brief = '''Edits an existing RSVP event message.''',
                  help  = '''Edits an existing RSVP event message. Only the owner of the message can edit
                           the message. The entire message is replaced and reparsed during this command.''',
                  usage = '''<systemID> <msg>''')
    async def edit(self, ctx, *, arg):
        # Ignore ourselves
        if ctx.author == self.bot.user:
            return

        # Split the arguments, the first should be the message ID and the second is the string
        # that will become the message
        splitArg = arg.split('\n', 1)

        # If we only got 1 thing, it might be all on the same line, so now break it up by spaces
        if len(splitArg) == 1:
            splitArg = splitArg[0].split(' ', 1)

        if len(splitArg) > 0:
            msgId = int(splitArg[0])
        else:
            print('RSVP Edit did not get a message ID, so we cant do anything. Skipping...')
            return

        if len(splitArg) > 1:
            msg = splitArg[1].strip()
        else:
            msg = None

        # Skip modifying anything if we aren't tracking on this message
        if msgId is None:
            print('could not find {:d} in the tracker so ignoring this'.format(msgId))
            return
        else:
            event = self.tracker.getTrackedItem(msgId)
            print(event)

        ## Only the owner is allowed to edit
        if ctx.author != event.owner:
            print('the called {} is not the owner {}'.format(ctx.author.display_name, event.owner.display_name))
            return

        # Update Emojis
        # TODO: There is an edge case here where the edit will remove existing reacts. We currently don't remove
        #       the ones we made ourselves, but we should probably consider it
        event.message = msg
        self.parseMsg(event)

        for e in event.cogData:
            for r in event.msgObj.reactions:
                if e == r.emoji:
                    break
            else:
                await event.msgObj.add_reaction(e)

        ## Update message
        await self.msgGenerator(event)

        ## Delete the modifying message
        await ctx.message.delete()

    @rsvp.command(brief = '''Deletes an existing RSVP event message.''',
                  help  = '''Deletes an existing RSVP message. Only the owner of the message can delete it.
                           Deletion is permanent and un-recoverable. On completion, the entire history of
                           the message is purged.''',
                  usage = '''<systemID>''')
    async def delete(self, ctx, arg):
        # Ignore ourselves
        if ctx.author == self.bot.user:
            return

        # Delete the modifying message to indicate that we've processed it
        await ctx.message.delete()

        # Attempt to coerce the arguments from a string to an int and perform a lookup for the ID
        try:
            msgId = int(arg)
        except:
            print('Failed to convert rsvp delete argument to delete. Got {}'.format(arg))
            return
        else:
            event = self.tracker.getTrackedItem(msgId)

        # Skip modifying anything if we aren't tracking this message
        if event is None:
            print('Could not find {:d} in the tracker so ignoring this'.format(msgId))
            return

        # Only the owner is allowed to delete
        if ctx.author != event.owner:
            print('Delete called by {} but is not the owner {}'.format(ctx.author.display_name, event.owner.display_name))
            return

        # Delete the message
        await event.msgObj.delete()
        self.tracker.deleteTrackedItem(msgId)

        # Debug
        print(self.rsvps)

    @rsvp.command(brief = '''Extends the duration of an existing RSVP event message.''',
                  help  = '''Extends the duration of an existing RSVP message. Only the owner of the message can
                           extend it. You can only add additional time, not remove. Extension is a set amount of
                           time (referred to as a time unit) and cannot be adjusted by the user. The quantity
                           provided is the number of time units to extend by.''',
                  usage = '''<systemID> <quantity>''')
    async def extend(self, ctx, sysId, qty):
        ## Ignore ourselves
        if ctx.author == self.bot.user:
            return

        # Delete the modifying message to indicate that we've processed it
        await ctx.message.delete()

        ## Attempt to coerce the arguments from strings to ints
        try:
            msgId     = int(sysId)
            timeUnits = int(qty)
        except:
            print('Failed to convert rsvp delete argument to delete. Got System ID: {}, Quantity: {}'.format(sysId, qty))
            return
        else:
            event = self.tracker.getTrackedItem(msgId)

        # Skip modifying anything if we aren't tracking this message
        if event is None:
            print('Could not find {:d} in the tracker so ignoring this'.format(msgId))
            return

        # Extend the message
        extTime = self.expireTimeExt * timeUnits
        event.expire += extTime

        # Reprint the message
        await self.msgGenerator(event)

        # Debug
        print(self.rsvps)