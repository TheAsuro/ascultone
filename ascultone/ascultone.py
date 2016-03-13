#!/usr/bin/env python3
import eventlet
eventlet.monkey_patch()

import os
import importlib
import logging
import re
import sqlite3
import sys


from .irc import IrcBot
from .channel import Channel


class Ascultone(IrcBot):
    logger = logging.getLogger(__name__)
    command_validator = re.compile(
        r"^\w+$"
    )

    def __init__(self, config):
        super().__init__(config)
        self.on_join  = []
        self.commands = {}
        self.triggers = {}
        self.modules  = []
        self.database = sqlite3.connect(config["database"])
        self.cursor   = self.database.cursor()

    def register_join(self, function):
        self.on_join.append(function)

    def register_command(self, command, function, param_size=None):
        assert self.command_validator.match(command) is not None
        self.commands[command] = (function, param_size)

    def register_trigger(self, trigger, function):
        self.triggers[re.compile(trigger)] = function

    def quit(self, reason=None):
        super().quit(reason)
        self.database.commit()

    def start(self):
        if not self.connected:
            self._connect()
        for module in self.config.get("modules", []):
            self.load_file(module)
        try:
            self.mainloop()
        finally:
            self.quit(self.config.get("quit_message", self.config["realname"]))

    def load_file(self, filename):
        self.logger.info("Loading module '%s'...", filename)
        # This whole song and dance is to that `importlib.import_module`
        # imports the files from the module folder too.
        filedir = os.path.abspath(os.path.dirname(filename))
        modulename = os.path.splitext(os.path.basename(filename))[0]
        sys.path.insert(0, filedir)
        module = importlib.import_module(modulename)
        self.modules.append(module)
        module.init_module(self)
        # But of course we don't want modules to import from the module folder
        # after loading one module.
        sys.path = sys.path[1:]

    def _handle_message(self, message):
        if message.command == "JOIN":
            for func in self.on_join:
                sender = message.sender
                channel = Channel(message.params[0])
                func(self, sender, channel)
        elif message.command == "PRIVMSG":
            command_match = re.match(r"{}!?"
                                     r"\s+(?P<command>\w+)"
                                     r"(?: (?P<params>.+))?"
                                     .format(self.nickname),
                                     message.params[1])
            if command_match is not None:
                group_dict = command_match.groupdict()
                params = (group_dict["params"] or "").split(" ")
                self.logger.info("Got command '%s' with params %s",
                                 group_dict,
                                 (group_dict["params"] or "").split(" "))
                if group_dict["command"] in self.commands:
                    function, param_len = self.commands[group_dict["command"]]
                    # I have zero idea how to indent this
                    if isinstance(param_len, int) and \
                            param_len != len(params):
                        return
                    if isinstance(param_len, (tuple, list, set)) and \
                            len(params) not in param_len:
                        return
                    eventlet.spawn_n(
                        function,
                        self,
                        message.find_source(self),
                        message.sender,
                        params
                    )
            else:
                for trigger, response in self.triggers.items():
                    if trigger.search(message.params[1]) is None:
                        continue
                    self.logger.info("Matched trigger '%s' to message '%s'",
                                     trigger.pattern,
                                     message.params[1])
                    eventlet.spawn_n(
                        response,
                        self,
                        message.find_source(self),
                        message.sender,
                        message.params[1]
                    )
                    break
