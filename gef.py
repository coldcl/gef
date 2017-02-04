#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#
#######################################################################################
# GEF - Multi-Architecture GDB Enhanced Features for Exploiters & Reverse-Engineers
#
# by  @_hugsy_
#
#######################################################################################
#
# GEF is a kick-ass set of commands for X86, ARM, MIPS, PowerPC and SPARC to
# make GDB cool again for exploit dev. It is aimed to be used mostly by exploit
# devs and reversers, to provides additional features to GDB using the Python
# API to assist during the process of dynamic analysis.
#
# GEF fully relies on GDB API and other Linux-specific sources of information
# (such as /proc/<pid>). As a consequence, some of the features might not work
# on custom or hardened systems such as GrSec.
#
# It has full support for both Python2 and Python3 and works on
#   * x86-32 & x86-64
#   * arm v5,v6,v7
#   * aarch64 (armv8)
#   * mips & mips64
#   * powerpc & powerpc64
#   * sparc & sparc64(v9)
#
# Requires GDB 7.x compiled with Python (2.x, or 3.x)
#
# To start: in gdb, type `source /path/to/gef.py`
#
#######################################################################################
#
# gef is distributed under the MIT License (MIT)
# Copyright (c) 2013-2017 crazy rabbidz
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
#

from __future__ import print_function, division

import abc
import binascii
import collections
import ctypes
import fcntl
import functools
import getopt
import hashlib
import imp
import inspect
import itertools
import os
import platform
import re
import resource
import socket
import string
import struct
import subprocess
import sys
import tempfile
import termios
import time
import traceback
import types

PYTHON_MAJOR = sys.version_info[0]

if PYTHON_MAJOR == 2:
    from HTMLParser import HTMLParser
    from cStringIO import StringIO
    from urllib import urlopen
    import ConfigParser as configparser
    import xmlrpclib

    # Compat Py2/3 hacks
    range = xrange

    left_arrow = "<-"
    right_arrow = "->"
    down_arrow = "\\->"
    horizontal_line = "-"
    vertical_line = "|"

    gef_prompt = "gef> "
    gef_prompt_on = "\001\033[1;32m\002{0:s}\001\033[0m\002".format(gef_prompt)
    gef_prompt_off = "\001\033[1;31m\002{0:s}\001\033[0m\002".format(gef_prompt)

elif PYTHON_MAJOR == 3:
    from html.parser import HTMLParser
    from io import StringIO
    from urllib.request import urlopen
    import configparser
    import xmlrpc.client as xmlrpclib

    # Compat Py2/3 hack
    long = int
    unicode = str
    FileNotFoundError = IOError

    left_arrow = " \u2190 "
    right_arrow = " \u2192 "
    down_arrow = "\u21b3"
    horizontal_line = "\u2500"
    vertical_line = "\u2502"

    gef_prompt = "gef\u27a4  "
    gef_prompt_on = "\001\033[1;32m\002{0:s}\001\033[0m\002".format(gef_prompt)
    gef_prompt_off = "\001\033[1;31m\002{0:s}\001\033[0m\002".format(gef_prompt)

else:
    raise Exception("WTF is this Python version??")


def http_get(url):
    """Basic HTTP wrapper for GET request. Returns the body of the page if HTTP code is OK,
    else returns None."""
    try:
        http = urlopen(url)
        if http.getcode() != 200:
            return None
        return http.read()
    except Exception:
        return None


def update_gef(argv):
    """Tries to update `gef` to the latest version pushed on GitHub. Returns 0 on success,
    1 on failure. """
    gef_local = os.path.realpath(argv[0])
    hash_gef_local = hashlib.sha512(open(gef_local, "rb").read()).digest()
    gef_remote = "https://raw.githubusercontent.com/hugsy/gef/master/gef.py"
    gef_remote_data = http_get(gef_remote)
    if gef_remote_data is None:
        print("[-] Failed to get remote gef")
        return 1

    hash_gef_remote = hashlib.sha512(gef_remote_data).digest()

    if hash_gef_local == hash_gef_remote:
        print("[-] No update")
    else:
        with open(gef_local, "wb") as f:
            f.write(gef_remote_data)
        print("[+] Updated")
    return 0


try:
    import gdb
except ImportError:
    # if out of gdb, the only action allowed is to update gef.py
    if len(sys.argv)==2 and sys.argv[1]=="--update":
        sys.exit( update_gef(sys.argv) )
    print("[-] gef cannot run as standalone")
    sys.exit(0)

__aliases__                            = []
__config__                             = {}
__infos_files__                        = []
__loaded__                             = []
__missing__                            = {}
__gef_convenience_vars_index__         = 0

DEFAULT_PAGE_ALIGN_SHIFT               = 12
DEFAULT_PAGE_SIZE                      = 1 << DEFAULT_PAGE_ALIGN_SHIFT
GEF_RC                                 = os.path.join(os.getenv("HOME"), ".gef.rc")
GEF_TEMP_DIR                           = os.path.join(tempfile.gettempdir(), "gef")


class GefGenericException(Exception):
    """GEF generic exception."""
    def __init__(self, value):
        self.message = value
        return

    def __str__(self):
        return repr(self.message)


class GefMissingDependencyException(GefGenericException):
    pass


class GefUnsupportedMode(GefGenericException):
    pass


class GefUnsupportedOS(GefGenericException):
    pass


if PYTHON_MAJOR==3:
    lru_cache = functools.lru_cache
else:
    def lru_cache(maxsize = 128):
        """https://gist.github.com/hugsy/f327097d905f78c1e253c0d87a235b41"""
        class GefLruCache(object):
            """Local LRU cache for Python2"""
            def __init__(self, input_func, max_size):
                self._input_func        = input_func
                self._max_size          = max_size
                self._caches_dict       = {}
                self._caches_info       = {}
                return

            def cache_info(self, caller=None):
                if caller not in self._caches_dict:
                    return
                hits = self._caches_info[caller]["hits"]
                missed = self._caches_info[caller]["missed"]
                cursz = len(self._caches_dict[caller])
                return "CacheInfo(hits={}, misses={}, maxsize={}, currsize={})".format(hits, missed, self._max_size, cursz)

            def cache_clear(self, caller=None):
                if caller in self._caches_dict:
                    del self._caches_dict[caller]
                    self._caches_dict[caller] = collections.OrderedDict()
                return

            def __get__(self, obj, objtype):
                return_func = functools.partial(self._cache_wrapper, obj)
                return_func.cache_clear = functools.partial(self.cache_clear, obj)
                return functools.wraps(self._input_func)(return_func)

            def __call__(self, *args, **kwargs):
                return self._cache_wrapper(None, *args, **kwargs)

            __call__.cache_clear = cache_clear
            __call__.cache_info  = cache_info

            def _cache_wrapper(self, caller, *args, **kwargs):
                kwargs_key = "".join(map(lambda x : str(x) + str(type(kwargs[x])) + str(kwargs[x]), sorted(kwargs)))
                key = "".join(map(lambda x : str(type(x)) + str(x) , args)) + kwargs_key
                if caller not in self._caches_dict:
                    self._caches_dict[caller] = collections.OrderedDict()
                    self._caches_info[caller] = {"hits":0, "missed":0}

                cur_caller_cache_dict = self._caches_dict[caller]
                if key in cur_caller_cache_dict:
                    self._caches_info[caller]["hits"] += 1
                    return cur_caller_cache_dict[key]

                self._caches_info[caller]["missed"] += 1
                if self._max_size is not None:
                    if len(cur_caller_cache_dict) >= self._max_size:
                        cur_caller_cache_dict.popitem(False)

                cur_caller_cache_dict[key] = self._input_func(caller, *args, **kwargs) if caller != None else self._input_func(*args, **kwargs)
                return cur_caller_cache_dict[key]

        return (lambda input_func : functools.wraps(input_func)(GefLruCache(input_func, maxsize)))


def reset_all_caches():
    """Free all memoized values."""
    for mod in dir(sys.modules["__main__"]):
        obj = getattr(sys.modules["__main__"], mod)
        if hasattr(obj, "cache_clear"):
            obj.cache_clear()
    return


class Color:
    """Colorify class."""
    colors = {
        "normal"         : "\033[0m",
        "gray"           : "\033[1;30m",
        "red"            : "\033[31m",
        "green"          : "\033[32m",
        "yellow"         : "\033[33m",
        "blue"           : "\033[34m",
        "pink"           : "\033[35m",
        "bold"           : "\033[1m",
        "underline"      : "\033[4m",
        "underline_off"  : "\033[24m",
        "highlight"      : "\033[3m",
        "highlight_off"  : "\033[23m",
        "blink"          : "\033[5m",
        "blink_off"      : "\033[25m",
    }

    @staticmethod
    def redify(msg):       return Color.colorify(msg, attrs="red")
    @staticmethod
    def greenify(msg):     return Color.colorify(msg, attrs="green")
    @staticmethod
    def blueify(msg):      return Color.colorify(msg, attrs="blue")
    @staticmethod
    def yellowify(msg):    return Color.colorify(msg, attrs="yellow")
    @staticmethod
    def grayify(msg):      return Color.colorify(msg, attrs="gray")
    @staticmethod
    def pinkify(msg):      return Color.colorify(msg, attrs="pink")
    @staticmethod
    def boldify(msg):      return Color.colorify(msg, attrs="bold")
    @staticmethod
    def underlinify(msg):  return Color.colorify(msg, attrs="underline")
    @staticmethod
    def highlightify(msg): return Color.colorify(msg, attrs="highlight")
    @staticmethod
    def blinkify(msg):     return Color.colorify(msg, attrs="blink")

    @staticmethod
    def colorify(text, attrs):
        if __config__["theme.disable_color"][0] in ("1", "True"):
            return text
        msg = []
        colors = Color.colors
        for attr in attrs.split():
            if attr in colors: msg.append(colors[attr])
        msg.append(text)
        if colors["highlight"] in msg :   msg.append(colors["highlight_off"])
        if colors["underline"] in msg :   msg.append(colors["underline_off"])
        if colors["blink"] in msg :       msg.append(colors["blink_off"])
        msg.append(colors["normal"])
        return "".join(msg)


class Address:
    def __init__(self, *args, **kwargs):
        self.value = kwargs.get("value", 0)
        self.section = kwargs.get("section", None)
        self.info = kwargs.get("info", None)
        self.valid = kwargs.get("valid", True)
        return

    def __str__(self):
        return hex(self.value)

    def is_in_text_segment(self):
        return hasattr(self.info, "name") and ".text" in self.info.name

    def is_in_stack_segment(self):
        return hasattr(self.info, "name") and "[stack]" in self.info.name

    def is_in_heap_segment(self):
        return hasattr(self.info, "name") and "[heap]" in self.info.name

    def dereference(self):
        addr = align_address(long(self.value))
        addr = dereference(addr)
        return long(addr)


class Permission:
    NONE      = 0
    READ      = 1
    WRITE     = 2
    EXECUTE   = 4
    ALL       = READ | WRITE | EXECUTE

    def __init__(self, *args, **kwargs):
        self.value = kwargs.get("value", 0)
        return

    def __or__(self, value):
        return self.value | value

    def __and__(self, value):
        return self.value & value

    def __xor__(self, value):
        return self.value ^ value

    def __eq__(self, value):
        return self.value == value

    def __ne__(self, value):
        return self.value != value

    def __str__(self):
        perm_str = ""
        perm_str += "r" if self & Permission.READ else "-"
        perm_str += "w" if self & Permission.WRITE else "-"
        perm_str += "x" if self & Permission.EXECUTE else "-"
        return perm_str

    @staticmethod
    def from_info_sections(*args):
        perm = Permission()
        for arg in args:
            if "READONLY" in arg:
                perm.value += Permission.READ
            if "DATA" in arg:
                perm.value += Permission.WRITE
            if "CODE" in arg:
                perm.value += Permission.EXECUTE
        return perm

    @staticmethod
    def from_process_maps(perm_str):
        perm = Permission()
        if perm_str[0] == "r":
            perm.value += Permission.READ
        if perm_str[1] == "w":
            perm.value += Permission.WRITE
        if perm_str[2] == "x":
            perm.value += Permission.EXECUTE
        return perm


class Section:
    page_start      = None
    page_end        = None
    offset          = None
    permission      = None
    inode           = None
    path            = None

    def __init__(self, *args, **kwargs):
        attrs = ["page_start", "page_end", "offset", "permission", "inode", "path"]
        for attr in attrs:
            value = kwargs[attr] if attr in kwargs else None
            setattr(self, attr, value)
        return

    def is_readable(self):
        return self.permission.value and self.permission.value&Permission.READ

    def is_writable(self):
        return self.permission.value and self.permission.value&Permission.WRITE

    def is_executable(self):
        return self.permission.value and self.permission.value&Permission.EXECUTE

    @property
    def size(self):
        if self.page_end is None or self.page_start is None:
            return -1
        return self.page_end - self.page_start


class Zone:
    name              = None
    zone_start        = None
    zone_end          = None
    filename          = None


class Elf:
    """
    Basic ELF parsing based on http://www.skyfree.org/linux/references/ELF_Format.pdf
    """
    e_magic           = None
    e_class           = None
    e_endianness      = None
    e_eiversion       = None
    e_osabi           = None
    e_abiversion      = None
    e_pad             = None
    e_type            = None
    e_machine         = None
    e_version         = None
    e_entry           = None
    e_phoff           = None
    e_shoff           = None
    e_flags           = None
    e_ehsize          = None
    e_phentsize       = None
    e_phnum           = None
    e_shentsize       = None
    e_shnum           = None
    e_shstrndx        = None

    BIG_ENDIAN        = 0
    LITTLE_ENDIAN     = 1

    ELF_32_BITS       = 0x01
    ELF_64_BITS       = 0x02

    X86_64            = 0x3e
    X86_32            = 0x03
    ARM               = 0x28
    MIPS              = 0x08
    POWERPC           = 0x14 # http://refspecs.freestandards.org/elf/elfspec_ppc.pdf
    POWERPC64         = 0x15 # http://refspecs.linuxfoundation.org/ELF/ppc64/PPC-elf64abi.html
    SPARC             = 0x02
    SPARC64           = 0x2b
    AARCH64           = 0xb7


    def __init__(self, elf):

        if not os.access(elf, os.R_OK):
            err("'{0}' not found/readable".format(elf))
            err("Failed to get file debug information, most of gef features will not work")
            return

        with open(elf, "rb") as fd:
            # off 0x0
            self.e_magic, self.e_class, self.e_endianness, self.e_eiversion = struct.unpack(">IBBB", fd.read(7))

            # adjust endianness in bin reading
            endian = "<" if self.e_endianness == Elf.LITTLE_ENDIAN else ">"

            # off 0x7
            self.e_osabi, self.e_abiversion = struct.unpack("{}BB".format(endian), fd.read(2))
            # off 0x9
            self.e_pad = fd.read(7)
            # off 0x10
            self.e_type, self.e_machine, self.e_version = struct.unpack("{}HHI".format(endian), fd.read(8))
            # off 0x18
            if self.e_class == Elf.ELF_64_BITS:
                # if arch 64bits
                self.e_entry, self.e_phoff, self.e_shoff = struct.unpack("{}QQQ".format(endian), fd.read(24))
            else:
                # else arch 32bits
                self.e_entry, self.e_phoff, self.e_shoff = struct.unpack("{}III".format(endian), fd.read(12))

            self.e_flags, self.e_ehsize, self.e_phentsize, self.e_phnum = struct.unpack("{}HHHH".format(endian), fd.read(8))
            self.e_shentsize, self.e_shnum, self.e_shstrndx = struct.unpack("{}HHH".format(endian), fd.read(6))

        return


class Instruction:
    """GEF representation of instruction."""

    def __init__(self, address, location, mnemo, operands):
        self.address, self.location, self.mnemo, self.operands = address, location, mnemo, operands
        return

    def __str__(self):
        return "{:#x}   {}    {} {}".format(self.address,
                                            self.location,
                                            self.mnemo,
                                            ", ".join(self.operands))

    def is_valid(self):
        return "(bad)" not in self.mnemo


class GlibcArena:
    """
    Glibc arena class
    Ref: https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1671
    """
    def __init__(self, addr=None):
        arena = gdb.parse_and_eval(addr)
        self.__arena = arena.cast(gdb.lookup_type("struct malloc_state"))
        self.__addr = long(arena.address)
        self.__arch = long(get_memory_alignment())
        return

    def __getitem__(self, item):
        return self.__arena[item]

    def __getattr__(self, item):
        return self.__arena[item]

    def __int__(self):
        return self.__addr

    def deref_as_long(self, addr):
        naddr = dereference(addr).address
        return long(naddr)

    def fastbin(self, i):
        addr = self.deref_as_long(self.fastbinsY[i])
        if addr == 0:
            return None
        return GlibcChunk(addr + 2 * self.__arch)

    def bin(self, i):
        idx = i * 2
        fd = self.deref_as_long(self.bins[idx])
        bw = self.deref_as_long(self.bins[idx + 1])
        return (fd, bw)

    def get_next(self):
        addr_next = self.deref_as_long(self.next)
        arena_main = GlibcArena("main_arena")
        if addr_next == arena_main.__addr:
            return None
        return GlibcArena("*{:#x} ".format(addr_next))

    def get_arch(self):
        return self.__arch

    def __str__(self):
        top             = self.deref_as_long(self.top)
        last_remainder  = self.deref_as_long(self.last_remainder)
        n               = self.deref_as_long(self.next)
        nfree           = self.deref_as_long(self.next_free)
        sysmem          = long(self.system_mem)

        fmt = "Arena (base={:#x}, top={:#x}, last_remainder={:#x}, next={:#x}, next_free={:#x}, system_mem={:#x})"
        return fmt.format(self.__addr, top, last_remainder, n, nfree. sysmem)


class GlibcChunk:
    """Glibc chunk class.
    Ref:  https://sploitfun.wordpress.com/2015/02/10/understanding-glibc-malloc/"""

    def __init__(self, addr, from_base=False):
        """Init `addr` as a chunk"""
        self.arch = int(get_memory_alignment())
        if from_base:
            self.start_addr = addr
            self.addr = addr + 2 * self.arch
        else:
            self.start_addr = int(addr - 2 * self.arch)
            self.addr = addr

        self.size_addr  = int(self.addr - self.arch)
        self.prev_size_addr = self.start_addr
        return

    def get_chunk_size(self):
        return read_int_from_memory(self.size_addr) & (~0x03)

    def get_usable_size(self):
        # https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L4537
        cursz = self.get_chunk_size()
        if cursz == 0: return cursz
        if self.has_M_bit(): return cursz - 2 * self.arch
        return cursz - self.arch

    def get_prev_chunk_size(self):
        return read_int_from_memory(self.prev_size_addr)


    def get_next_chunk(self):
        addr = self.addr + self.get_chunk_size()
        return GlibcChunk(addr)


    # if free-ed functions
    def get_fwd_ptr(self):
        return read_int_from_memory(self.addr)

    def get_bkw_ptr(self):
        return read_int_from_memory(self.addr + self.arch)
    # endif free-ed functions


    def has_P_bit(self):
        """Check for in PREV_INUSE bit
        Ref: https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1267"""
        return read_int_from_memory(self.size_addr) & 0x01

    def has_M_bit(self):
        """Check for in IS_MMAPPED bit
        Ref: https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1274"""
        return read_int_from_memory(self.size_addr) & 0x02

    def has_N_bit(self):
        """Check for in NON_MAIN_ARENA bit.
        Ref: https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1283"""
        return read_int_from_memory(self.size_addr) & 0x04

    def is_used(self):
        """
        Check if the current block is used by:
        - checking the M bit is true
        - or checking that next chunk PREV_INUSE flag is true
        """
        if self.has_M_bit():
            return True

        next_chunk = self.get_next_chunk()
        return True if next_chunk.has_P_bit() else False


    def str_chunk_size_flag(self):
        msg = []
        msg += "PREV_INUSE flag: {}".format(Color.greenify("On") if self.has_P_bit() else Color.redify("Off"))
        msg += "IS_MMAPPED flag: {}".format(Color.greenify("On") if self.has_M_bit() else Color.redify("Off"))
        msg += "NON_MAIN_ARENA flag: {}".format(Color.greenify("On") if self.has_N_bit() else Color.redify("Off"))
        return "\n".join(msg)


    def _str_sizes(self):
        msg = []
        failed = False

        try:
            msg += "Chunk size: {0:d} ({0:#x})".format(self.get_chunk_size())
            msg += "Usable size: {0:d} ({0:#x})".format(self.get_usable_size())
            failed = True
        except gdb.MemoryError:
            msg += "Chunk size: Cannot read at {:#x} (corrupted?)".format(self.size_addr)

        try:
            msg += "Previous chunk size: {0:d} ({0:#x})".format(self.get_prev_chunk_size())
            failed = True
        except gdb.MemoryError:
            msg += "Previous chunk size: Cannot read at {:#x} (corrupted?)".format(self.start_addr)

        if failed:
            msg += self.str_chunk_size_flag()

        return "\n".join(msg)

    def _str_pointers(self):
        fwd = self.addr
        bkw = self.addr + self.arch

        msg = []

        try:
            msg += "Forward pointer: {0:#x}".format(self.get_fwd_ptr())
        except gdb.MemoryError:
            msg += "Forward pointer: {0:#x} (corrupted?)".format(fwd)

        try:
            msg += "Backward pointer: {0:#x}".format(self.get_bkw_ptr())
        except gdb.MemoryError:
            msg += "Backward pointer: {0:#x} (corrupted?)".format(bkw)

        return "\n".join(msg)

    def str_as_alloced(self):
        return self._str_sizes()

    def str_as_freeed(self):
        return "{}\n\n{}".format(self._str_sizes(), self._str_pointers())

    def __str__(self):
        m = []
        m += Color.greenify("FreeChunk") if not self.is_used() else Color.redify("UsedChunk")
        m += "(addr={:#x},size={:#x})".format(long(self.addr),self.get_chunk_size())
        return "".join(m)

    def pprint(self):
        msg = []
        if not self.is_used():
            msg += titlify("Chunk (free): {:#x}".format(self.start_addr), Color.GREEN)
            msg += self.str_as_freeed()
        else:
            msg += titlify("Chunk (used): {:#x}".format(self.start_addr), Color.RED)
            msg += self.str_as_alloced()

        gdb.write("\n".join(msg))
        gdb.flush()
        return


def titlify(text, color=None, msg_color=None):
    cols = get_terminal_size()[1]
    nb = (cols - len(text) - 4)//2
    if color is None:
        color = __config__.get("theme.default_title_line")[0]
    if msg_color is None:
        msg_color = __config__.get("theme.default_title_message")[0]

    msg = []
    msg.append(Color.colorify(horizontal_line * nb + '[ ', attrs=color))
    msg.append(Color.colorify(text, attrs=msg_color))
    msg.append(Color.colorify(' ]' + horizontal_line * nb, attrs=color))
    return "".join(msg)


def _xlog(text, stream, cr=True):
    text += "\n" if cr else ""
    gdb.write(text, stream)
    if cr:
        gdb.flush()
    return 0


def err(msg, cr=True):   return _xlog("{} {}".format(Color.colorify("[!]", attrs="bold red"), msg), gdb.STDERR, cr)
def warn(msg, cr=True):  return _xlog("{} {}".format(Color.colorify("[*]", attrs="bold yellow"), msg), gdb.STDLOG, cr)
def ok(msg, cr=True):    return _xlog("{} {}".format(Color.colorify("[+]", attrs="bold green"), msg), gdb.STDLOG, cr)
def info(msg, cr=True):  return _xlog("{} {}".format(Color.colorify("[+]", attrs="bold blue"), msg), gdb.STDLOG, cr)


def show_last_exception():
    exc_type, exc_value, exc_traceback = sys.exc_info()
    traceback.print_tb(exc_traceback, limit=1, file=sys.stdout)
    traceback.print_exception(exc_type, exc_value, exc_traceback,limit=5, file=sys.stdout)
    return


def gef_pystring(x):
    if PYTHON_MAJOR == 3:
        return str(x, encoding="ascii")
    return x


def gef_pybytes(x):
    if PYTHON_MAJOR == 3:
        return bytes(str(x), encoding="utf-8")
    return x


@lru_cache()
def which(program):
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath = os.path.split(program)[0]
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            path = path.strip('"')
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    raise IOError("Missing file `{:s}`".format(program))


def hexdump(source, length=0x10, separator=".", show_raw=False, base=0x00):
    """
    Return the hexdump of `src` argument.
    @param source *MUST* be of type bytes or bytearray
    @param length is the length of items per line
    @param separator is the default character to use if one byte is not printable
    @param show_raw if True, do not add the line nor the text translation
    @param base is the start address of the block being hexdump
    @param func is the function to use to parse bytes (int for Py3, chr for Py2)
    @return a string with the hexdump
    """
    result = []
    for i in range(0, len(source), length):
        s = source[i:i + length]

        if PYTHON_MAJOR == 3:
            hexa = " ".join(["{:02x}".format(c) for c in s])
            text = "".join([chr(c) if 0x20 <= c < 0x7F else separator for c in s])
        else:
            hexa = " ".join(["{:02x}".format(ord(c)) for c in s])
            text = "".join([c if 0x20 <= ord(c) < 0x7F else separator for c in s])

        if show_raw:
            result.append(hexa)
        else:
            result.append("{addr:#0{aw}x}     {data:<{dw}}    {text}".format(aw=18, addr=base + i, dw=3 * length, data=hexa, text=text))

    return "\n".join(result)


def is_debug():
    return "gef.debug" in __config__ and __config__["gef.debug"][0] is True


def enable_redirect_output(to_file="/dev/null"):
    gdb.execute("set logging overwrite")
    gdb.execute("set logging file {:s}".format(to_file))
    gdb.execute("set logging redirect on")
    gdb.execute("set logging on")
    return


def disable_redirect_output():
    gdb.execute("set logging redirect off")
    gdb.execute("set logging off")
    return


def gef_makedirs(path, mode=0o755):
    abspath = os.path.realpath(path)
    if os.path.isdir(abspath):
        return abspath

    if PYTHON_MAJOR == 3:
        os.makedirs(path, mode=mode, exist_ok=True)
    else:
        try:
            os.makedirs(path, mode=mode)
        except os.error:
            pass
    return abspath


@lru_cache(maxsize=None)
def gdb_lookup_symbol(name):
    try:
        return gdb.decode_line(name)[1][0]
    except gdb.error as err:
        return None


def gdb_disassemble(start_pc, **kwargs):
    """Disassemble instructions from `start_pc` (Integer). Accepts the following named parameters:
    - `end_pc` (Integer) to disassemble until this address
    - `count` (Integer) to disassemble this number of instruction.
    If `end_pc` and `count` are not provided, the function will behave as if `count=1`.
    Returns an iterator of Instruction objects
    """
    frame = gdb.selected_frame()
    arch = frame.architecture()

    name = frame.name()
    if name:
        base = long(gdb.parse_and_eval("'{}'".format(name)).address)
        off  = start_pc - base

    for insn in arch.disassemble(start_pc, **kwargs):
        address = insn["addr"]
        asm = insn["asm"].rstrip()
        if " " in asm:
            mnemo, operands = asm.split(None, 1)
            operands = operands.split(",")
        else:
            mnemo, operands = asm, []

        if name and off >= 0:
            location = "<{}+{}>".format(name, off)
            off += insn["length"]
        else:
            location = ""

        yield Instruction(address, location, mnemo, operands)


def gdb_get_nth_previous_instruction_address(addr, n):
    """Returns the address (Integer) of the `n`-th instruction before `addr`."""
    # fixed-length ABI
    if not (is_x86_32() or is_x86_64()):
        if is_aarch64() or is_ppc64() or is_sparc64():
            insn_len = 4
        elif is_arm_thumb():
            insn_len = 2
        else:
            insn_len = get_memory_alignment()
        return addr - n*insn_len

    # variable-length ABI
    next_insn_addr = gef_next_instruction(addr).address
    cur_insn_addr  = gef_current_instruction(addr).address

    # we try to find a good set of previous instructions by "guessing" disassembling backwards
    for i in range(32*n, 1, -1):
        try:
            insns = list(gdb_disassemble(addr-i, end_pc=next_insn_addr))
        except gdb.MemoryError:
            # we can hit an unmapped page trying to read backward, if so just print forward disass lines
            break

        # 1. check all instructions are valid
        for x in insns:
            if not x.is_valid():
                continue

        # 2. if cur_insn is not at the end of the set, it is invalid
        last_insn = insns[-1]
        if last_insn.address == cur_insn_addr:
            return insns[-n-1].address

    return -1


def gdb_get_nth_next_instruction_address(addr, n):
    """Returns the address (Integer) of the `n`-th instruction after `addr`."""
    # fixed-length ABI
    if not (is_x86_32() or is_x86_64()):
        if is_aarch64() or is_ppc64() or is_sparc64():
            insn_len = 4
        elif is_arm_thumb():
            insn_len = 2
        else:
            insn_len = get_memory_alignment()
        return addr + n*insn_len

    # variable-length ABI
    insn = list(gdb_disassemble(addr, count=n))[-1]
    return insn.address


def gef_instruction_n(addr, n):
    """Returns the `n`-th instruction after `addr` as an Instruction object."""
    return list(gdb_disassemble(addr, count=n+1))[n-1]


def gef_current_instruction(addr):
    """Returns the current instruction as an Instruction object."""
    return gef_instruction_n(addr, 0)


def gef_next_instruction(addr):
    """Returns the next instruction as an Instruction object."""
    return gef_instruction_n(addr, 1)


def gef_disassemble(addr, nb_insn, from_top=False):
    """Disassemble `nb_insn` instructions after `addr`. If `from_top` is False (default), it will
    also disassemble the `nb_insn` instructions before `addr`.
    Returns an iterator of Instruction objects."""
    if (nb_insn & 1) == 1:
        count = nb_insn + 1

    if not from_top:
        start_addr = gdb_get_nth_previous_instruction_address(addr, count)
        if start_addr > 0:
            for insn in gdb_disassemble(start_addr, count=nb_insn):
                yield insn

    for insn in gdb_disassemble(addr, count=count):
        yield insn


def gef_execute_external(command, as_list=False, *args, **kwargs):
    res = subprocess.check_output(command, stderr=subprocess.STDOUT, shell=kwargs.get("shell", False))

    if as_list:
        lines = res.splitlines()
        return [gef_pystring(x) for x in lines]

    return gef_pystring(res)


def gef_execute_gdb_script(source):
    fd, fname = tempfile.mkstemp(suffix=".gdb", prefix="gef_")
    with os.fdopen(fd, "w") as f:
        f.write(source)
        f.flush()
    if os.access(fname, os.R_OK):
        gdb.execute("source {:s}".format(fname))
        os.unlink(fname)
    return


def check_security_property(opt, filename, pattern):
    cmd   = [which("readelf"),]
    cmd  += opt.split()
    cmd  += [filename,]
    lines = gef_execute_external(cmd).splitlines()

    for line in lines:
        if re.search(pattern, line):
            return True

    return False


@lru_cache()
def checksec(filename):
    """Global function to get the security properties of a binary."""
    try:
        which("readelf")
    except IOError:
        err("Missing `readelf`")
        return

    results = collections.OrderedDict()
    results["Canary"] = check_security_property("-s", filename, r"__stack_chk_fail") is True
    has_gnu_stack = check_security_property("-W -l", filename, r"GNU_STACK") is True
    if has_gnu_stack:
        results["NX"] = check_security_property("-W -l", filename, r"GNU_STACK.*RWE") is False
    else:
        results["NX"] = False
    results["PIE"] = check_security_property("-h", filename, r"Type:.*EXEC") is False
    results["Fortify"] = check_security_property("-s", filename, r"_chk@GLIBC") is True
    results["Partial RelRO"] = check_security_property("-l", filename, r"GNU_RELRO") is True
    results["Full RelRO"] = check_security_property("-d", filename, r"BIND_NOW") is True
    return results


def get_frame():
    return gdb.selected_inferior()


@lru_cache()
def get_arch():
    if not is_alive():
        return gdb.execute("show architecture", to_string=True).strip().split()[7][:-1]
    arch = gdb.selected_frame().architecture()
    return arch.name()


@lru_cache()
def get_endian():
    if gdb.execute("show endian", to_string=True).strip().split()[7] == "little" :
        return Elf.LITTLE_ENDIAN
    return Elf.BIG_ENDIAN


def is_big_endian():     return get_endian() == Elf.BIG_ENDIAN
def is_little_endian():  return not is_big_endian()


def flags_to_human(reg_value, value_table):
    flags = []
    for i in value_table:
        flag_str = Color.boldify(value_table[i].upper()) if reg_value & (1<<i) else value_table[i].lower()
        flags.append(flag_str)
    return "[{}]".format(" ".join(flags))


class Architecture(object):
    """Generic metaclass for the architecture supported by GEF."""
    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def all_registers(self):                       pass
    @abc.abstractproperty
    def nop_insn(self):                            pass
    @abc.abstractproperty
    def return_register(self):                     pass
    @abc.abstractproperty
    def flag_register(self):                       pass
    @abc.abstractproperty
    def flags_table(self):                         pass
    @abc.abstractproperty
    def function_parameters(self):                 pass
    @abc.abstractmethod
    def flag_register_to_human(self, val=None):    pass
    @abc.abstractmethod
    def is_call(self, insn):                       pass
    @abc.abstractmethod
    def is_conditional_branch(self, insn):         pass
    @abc.abstractmethod
    def is_branch_taken(self, insn):               pass

    @property
    def pc(self):
        try:
            return get_register("$pc")
        except Exception:
            return get_register_ex("$pc")

    @property
    def sp(self):
        try:
            return get_register("$sp")
        except Exception:
            return get_register_ex("$sp")


class ARM(Architecture):
    arch = "ARM"
    mode = "ARM"

    all_registers = ["$r0   ", "$r1   ", "$r2   ", "$r3   ", "$r4   ", "$r5   ", "$r6   ",
                     "$r7   ", "$r8   ", "$r9   ", "$r10  ", "$r11  ", "$r12  ", "$sp   ",
                     "$lr   ", "$pc   ", "$cpsr ",]

    # http://infocenter.arm.com/help/index.jsp?topic=/com.arm.doc.dui0041c/Caccegih.html
    # return b"\x00\x00\xa0\xe1" # mov r0,r0
    nop_insn = b"\x01\x10\xa0\xe1" # mov r1,r1
    return_register = "$r0"
    flag_register = "$cpsr"
    flags_table = {
        31: "negative",
        30: "zero",
        29: "carry",
        28: "overflow",
        7: "interrupt",
        6: "fast",
        5: "thumb"
    }
    function_parameters = ["$r0", "$r1", "$r2", "$r3"]

    def is_call(self, insn):
        return False

    def flag_register_to_human(self, val=None):
        # http://www.botskool.com/user-pages/tutorials/electronics/arm-7-tutorial-part-1
        if val is None:
            reg = self.flag_register
            val = get_register_ex(reg)
        return flags_to_human(val, self.flags_table)

    def is_conditional_branch(self, insn):
        branch_mnemos = {"beq", "bne", "bleq", "blt", "bgt", "bgez", "bvs", "bvc",
                  "jeq", "jne", "jleq", "jlt", "jgt", "jgez", "jvs", "jvc"}
        return insn.mnemo in branch_mnemos

    def is_branch_taken(self, insn):
        mnemo = insn.mnemo
        # ref: http://www.davespace.co.uk/arm/introduction-to-arm/conditional.html
        flags = dict((self.flags_table[k], k) for k in self.flags_table)
        val = get_register_ex(self.flag_register)
        taken, reason = False, ""

        if mnemo.endswith("eq"): taken, reason = val&(1<<flags["zero"]), "Z"
        elif mnemo.endswith("ne"): taken, reason = val&(1<<flags["zero"]) == 0, "!Z"
        elif mnemo.endswith("lt"): taken, reason = val&(1<<flags["negative"])!=val&(1<<flags["overflow"]), "N!=O"
        elif mnemo.endswith("le"): taken, reason = val&(1<<flags["zero"]) or val&(1<<flags["negative"])!=val&(1<<flags["overflow"]), "Z || N!=O"
        elif mnemo.endswith("gt"): taken, reason = val&(1<<flags["zero"]) == 0 and val&(1<<flags["negative"]) == val&(1<<flags["overflow"]), "!Z && N==O"
        elif mnemo.endswith("ge"): taken, reason = val&(1<<flags["negative"]) == val&(1<<flags["overflow"]), "N==O"
        elif mnemo.endswith("bvs"): taken, reason = val&(1<<flags["overflow"]), "O"
        elif mnemo.endswith("bvc"): taken, reason = val&(1<<flags["overflow"]) == 0, "!O"
        return taken, reason

    def mprotect_asm(self, addr, size, perm):
        _NR_mprotect = 125
        insns = [
            "push {r0-r2, r7}",
            "mov r0, {:d}".format(addr),
            "mov r1, {:d}".format(size),
            "mov r2, {:d}".format(perm),
            "mov r7, {:d}".format(_NR_mprotect),
            "svc 0",
            "pop {r0-r2, r7}",]
        return "; ".join(insns)


class AARCH64(ARM):
    arch = "ARM"
    mode = "AARCH64"

    all_registers = [
        "$x0       ", "$x1       ", "$x2       ", "$x3       ", "$x4       ", "$x5       ", "$x6       ", "$x7       ",
        "$x8       ", "$x9       ", "$x10      ", "$x11      ", "$x12      ", "$x13      ", "$x14      ", "$x15      ",
        "$x16      ", "$x17      ", "$x18      ", "$x19      ", "$x20      ", "$x21      ", "$x22      ", "$x23      ",
        "$x24      ", "$x25      ", "$x26      ", "$x27      ", "$x28      ", "$x29      ", "$x30      ", "$sp       ",
        "$pc       ", "$cpsr     ", "$fpsr     ", "$fpcr     ",]
    return_register = "$x0"
    flag_register = "$cpsr"
    flags_table = {
        31: "negative",
        30: "zero",
        29: "carry",
        28: "overflow",
        7: "interrupt",
        6: "fast"
    }
    function_parameters = ["$x0", "$x1", "$x2", "$x3"]

    def flag_register_to_human(self, val=None):
        # http://events.linuxfoundation.org/sites/events/files/slides/KoreaLinuxForum-2014.pdf
        reg = self.flag_register
        if not val:
            val = get_register_ex(reg)
        return flags_to_human(val, self.flags_table)

    def mprotect_asm(self, addr, size, perm):
        GefUnsupportedOS("Architecture {:s} not supported yet".format(self.arch))
        return

    def is_conditional_branch(self, insn):
        # https://www.element14.com/community/servlet/JiveServlet/previewBody/41836-102-1-229511/ARM.Reference_Manual.pdf
        # sect. 5.1.1
        mnemo = mnemo.insn
        branch_mnemos = {"cbnz", "cbz", "tbnz", "tbz"}
        return mnemo.startswith("b.") or mnemo in branch_mnemos

    def is_branch_taken(self, insn):
        mnemo, operands = insn.mnemo, insn.operands
        flags = dict((self.flags_table[k], k) for k in self.flags_table)
        val = get_register_ex(self.flag_register)
        taken, reason = False, ""

        if mnemo in {"cbnz", "cbz", "tbnz", "tbz"}:
            reg = operands[0]
            op = get_register(reg)
            if mnemo=="cbnz":
                if op!=0: taken, reason = True, "{}!=0".format(reg)
                else: taken, reason = False, "{}==0".format(reg)
            elif mnemo=="cbz":
                if op==0: taken, reason = True, "{}==0".format(reg)
                else: taken, reason = False, "{}!=0".format(reg)
            elif mnemo=="tbnz":
                i = int(operands[1])
                if (op & 1<<i) != 0: taken, reason = True, "{}&1<<{}!=0".format(reg,i)
                else: taken, reason = False, "{}&1<<{}==0".format(reg,i)
            elif mnemo=="tbz":
                i = int(operands[1])
                if (op & 1<<i) == 0: taken, reason = True, "{}&1<<{}==0".format(reg,i)
                else: taken, reason = False, "{}&1<<{}!=0".format(reg,i)

        if mnemo.endswith("eq"): taken, reason = val&(1<<flags["zero"]), "Z"
        if mnemo.endswith("ne"): taken, reason = val&(1<<flags["zero"]) == 0, "!Z"
        if mnemo.endswith("lt"): taken, reason = val&(1<<flags["negative"])!=val&(1<<flags["overflow"]), "N!=O"
        if mnemo.endswith("le"): taken, reason = val&(1<<flags["zero"]) or val&(1<<flags["negative"])!=val&(1<<flags["overflow"]), "Z || N!=O"
        if mnemo.endswith("gt"): taken, reason = val&(1<<flags["zero"]) == 0 and val&(1<<flags["negative"]) == val&(1<<flags["overflow"]), "!Z && N==O"
        if mnemo.endswith("ge"): taken, reason = val&(1<<flags["negative"]) == val&(1<<flags["overflow"]), "N==O"
        return taken, reason


class X86(Architecture):
    arch = "X86"
    mode = "32"

    nop_insn = b"\x90"
    all_registers = [
        "$eax   ", "$ebx   ", "$ecx   ", "$edx   ", "$esp   ", "$ebp   ", "$esi   ",
        "$edi   ", "$eip   ", "$cs    ", "$ss    ", "$ds    ", "$es    ",
        "$fs    ", "$gs    ", "$eflags",]
    return_register = "$eax"
    function_parameters = ["$esp",]
    flag_register = "$eflags"
    flags_table = {
        6: "zero",
        0: "carry",
        2: "parity",
        4: "adjust",
        7: "sign",
        8: "trap",
        9: "interrupt",
        10: "direction",
        11: "overflow",
        16: "resume",
        17: "virtualx86",
        21: "identification",
    }

    def flag_register_to_human(self, val=None):
        reg = self.flag_register
        if not val:
            val = get_register_ex(reg)
        return flags_to_human(val, self.flags_table)

    def is_call(self, insn):
        mnemo = insn.mnemo
        call_mnemos = {"call", "callq"}
        return mnemo in call_mnemos

    def is_conditional_branch(self, insn):
        mnemo = insn.mnemo
        branch_mnemos = {
            "ja", "jnbe", "jae", "jnb", "jnc", "jb", "jc", "jnae", "jbe", "jna",
            "jcxz", "jecxz", "jrcxz", "je", "jz", "jg", "jnle", "jge", "jnl",
            "jl", "jnge", "jle", "jng", "jne", "jnz", "jno", "jnp", "jpo", "jns",
            "jo", "jp", "jpe", "js"
        }
        return mnemo in branch_mnemos

    def is_branch_taken(self, insn):
        mnemo = insn.mnemo
        # all kudos to fG! (https://github.com/gdbinit/Gdbinit/blob/master/gdbinit#L1654)
        flags = dict((self.flags_table[k], k) for k in self.flags_table)
        val = get_register_ex(self.flag_register)
        cx = get_register_ex("$rcx") if self.mode == 64 else get_register_ex("$ecx")

        taken, reason = False, ""

        if mnemo in ("ja", "jnbe"):
            taken, reason = val&(1<<flags["carry"]) == 0 and val&(1<<flags["zero"]) == 0, "!C && !Z"
        elif mnemo in ("jae", "jnb", "jnc"):
            taken, reason = val&(1<<flags["carry"]) == 0, "!C"
        elif mnemo in ("jb", "jc", "jnae"):
            taken, reason = val&(1<<flags["carry"]), "C"
        elif mnemo in ("jbe", "jna"):
            taken, reason = val&(1<<flags["carry"]) or val&(1<<flags["zero"]), "C || Z"
        elif mnemo in ("jcxz", "jecxz", "jrcxz"):
            taken, reason = cx == 0, "!$CX"
        elif mnemo in ("je", "jz"):
            taken, reason = val&(1<<flags["zero"]), "Z"
        elif mnemo in ("jg", "jnle"):
            taken, reason = val&(1<<flags["zero"]) == 0 and val&(1<<flags["overflow"]) == val&(1<<flags["sign"]), "!Z && O==S"
        elif mnemo in ("jge", "jnl"):
            taken, reason = val&(1<<flags["sign"]) == val&(1<<flags["overflow"]), "S==O"
        elif mnemo in ("jl", "jnge"):
            taken, reason = val&(1<<flags["overflow"])!=val&(1<<flags["sign"]), "S!=O"
        elif mnemo in ("jle", "jng"):
            taken, reason = val&(1<<flags["zero"]) or val&(1<<flags["overflow"])!=val&(1<<flags["sign"]), "Z || S!=0"
        elif mnemo in ("jne", "jnz"):
            taken, reason = val&(1<<flags["zero"]) == 0, "!Z"
        elif mnemo in ("jno",):
            taken, reason = val&(1<<flags["overflow"]) == 0, "!O"
        elif mnemo in ("jnp", "jpo"):
            taken, reason = val&(1<<flags["parity"]) == 0, "!P"
        elif mnemo in ("jns",):
            taken, reason = val&(1<<flags["sign"]) == 0, "!S"
        elif mnemo in ("jo",):
            taken, reason = val&(1<<flags["overflow"]), "O"
        elif mnemo in ("jpe", "jp"):
            taken, reason = val&(1<<flags["parity"]), "P"
        elif mnemo in ("js",):
            taken, reason = val&(1<<flags["sign"]), "S"
        return taken, reason

    def mprotect_asm(self, addr, size, perm):
        _NR_mprotect = 125
        insns = [
            "pushad",
            "mov eax, {:d}".format(_NR_mprotect),
            "mov ebx, {:d}".format(addr),
            "mov ecx, {:d}".format(size),
            "mov edx, {:d}".format(perm),
            "int 0x80",
            "popad",]
        return "; ".join(insns)


class X86_64(X86):
    arch = "X86"
    mode = "64"

    all_registers = [
        "$rax   ", "$rbx   ", "$rcx   ", "$rdx   ", "$rsp   ", "$rbp   ", "$rsi   ",
        "$rdi   ", "$rip   ", "$r8    ", "$r9    ", "$r10   ", "$r11   ", "$r12   ",
        "$r13   ", "$r14   ", "$r15   ",
        "$cs    ", "$ss    ", "$ds    ", "$es    ", "$fs    ", "$gs    ", "$eflags",]
    return_register = "$rax"
    function_parameters = ["$rdi", "$rsi", "$rdx", "$rcx", "$r8", "$r9"]

    def mprotect_asm(self, addr, size, perm):
        _NR_mprotect = 10
        insns = ["push rax", "push rdi", "push rsi", "push rdx",
                 "mov rax, {:d}".format(_NR_mprotect),
                 "mov rdi, {:d}".format(addr),
                 "mov rsi, {:d}".format(size),
                 "mov rdx, {:d}".format(perm),
                 "syscall",
                 "pop rdx", "pop rsi", "pop rdi", "pop rax"]
        return "; ".join(insns)


class PowerPC(Architecture):
    arch = "PPC"
    mode = "PPC32"

    all_registers = [
        "$r0  ", "$r1  ", "$r2  ", "$r3  ", "$r4  ", "$r5  ", "$r6  ", "$r7  ",
        "$r8  ", "$r9  ", "$r10 ", "$r11 ", "$r12 ", "$r13 ", "$r14 ", "$r15 ",
        "$r16 ", "$r17 ", "$r18 ", "$r19 ", "$r20 ", "$r21 ", "$r22 ", "$r23 ",
        "$r24 ", "$r25 ", "$r26 ", "$r27 ", "$r28 ", "$r29 ", "$r30 ", "$r31 ",
        "$pc  ", "$msr ", "$cr  ", "$lr  ", "$ctr ", "$xer ", "$trap",]
    nop_insn = b"\x60\x00\x00\x00" # http://www.ibm.com/developerworks/library/l-ppc/index.html
    return_register = "$r0"
    flag_register = "$cr"
    flags_table = {
        3: "negative[0]",
        2: "positive[0]",
        1: "equal[0]",
        0: "overflow[0]",

        # cr7
        31: "less[7]",
        30: "greater[7]",
        29: "equal[7]",
        28: "overflow[7]",
    }
    function_parameters = ["$i0", "$i1", "$i2", "$i3", "$i4", "$i5"]

    def flag_register_to_human(self, val=None):
        # http://www.cebix.net/downloads/bebox/pem32b.pdf (% 2.1.3)
        if not val:
            reg = self.flag_register
            val = get_register_ex(reg)
        return flags_to_human(val, self.flags_table)

    def is_call(self, insn):
        return False

    def is_conditional_branch(self, insn):
        mnemo = insn.mnemo
        branch_mnemos = {"beq", "bne", "ble", "blt", "bgt", "bge"}
        return mnemo in branch_mnemos

    def is_branch_taken(self, insn):
        mnemo = insn.mnemo
        flags = dict((self.flags_table[k], k) for k in self.flags_table)
        val = get_register_ex(self.flag_register)
        taken, reason = False, ""
        if mnemo == "beq": taken, reason = val&(1<<flags["equal[7]"]), "E"
        elif mnemo == "bne": taken, reason = val&(1<<flags["equal[7]"]) == 0, "!E"
        elif mnemo == "ble": taken, reason = val&(1<<flags["equal[7]"]) or val&(1<<flags["less[7]"]), "E || L"
        elif mnemo == "blt": taken, reason = val&(1<<flags["less[7]"]), "L"
        elif mnemo == "bge": taken, reason = val&(1<<flags["equal[7]"]) or val&(1<<flags["greater[7]"]), "E || G"
        elif mnemo == "bgt": taken, reason = val&(1<<flags["greater[7]"]), "G"
        return taken, reason

    def mprotect_asm(self, addr, size, perm):
        """Ref: http://www.ibm.com/developerworks/library/l-ppc/index.html"""
        _NR_mprotect = 125
        insns = ["addi 1, 1, -16",                 # 1 = r1 = sp
                 "stw 0, 0(1)", "stw 3, 4(1)",     # r0 = syscall_code | r3, r4, r5 = args
                 "stw 4, 8(1)", "stw 5, 12(1)",
                 "li 0, {:d}".format(_NR_mprotect),
                 "lis 3, {:#x}@h".format(addr),
                 "ori 3, 3, {:#x}@l".format(addr),
                 "lis 4, {:#x}@h".format(size),
                 "ori 4, 4, {:#x}@l".format(size),
                 "li 5, {:d}".format(perm),
                 "sc",
                 "lwz 0, 0(1)", "lwz 3, 4(1)",
                 "lwz 4, 8(1)", "lwz 5, 12(1)",
                 "addi 1, 1, 16",]
        return ";".join(insns)


class PowerPC64(PowerPC):
    arch = "PPC"
    mode = "PPC64"


class SPARC(Architecture):
    """ Refs:
    - http://www.cse.scu.edu/~atkinson/teaching/sp05/259/sparc.pdf
    """
    arch = "SPARC"
    mode = ""

    all_registers = [
        "$g0 ", "$g1 ", "$g2 ", "$g3 ", "$g4 ", "$g5 ", "$g6 ", "$g7 ",
        "$o0 ", "$o1 ", "$o2 ", "$o3 ", "$o4 ", "$o5 ", "$o7 ",
        "$l0 ", "$l1 ", "$l2 ", "$l3 ", "$l4 ", "$l5 ", "$l6 ", "$l7 ",
        "$i0 ", "$i1 ", "$i2 ", "$i3 ", "$i4 ", "$i5 ", "$i7 ",
        "$pc ", "$npc", "$sp ", "$fp ", "$psr",]

    nop_insn = b"\x00\x00\x00\x00"  # sethi 0, %g0
    return_register = "$i0"
    flag_register = "$psr"
    flags_table = {
        23: "negative",
        22: "zero",
        21: "overflow",
        20: "carry",
        7: "supervisor",
        5: "trap",
    }
    function_parameters = ["$o0 ", "$o1 ", "$o2 ", "$o3 ", "$o4 ", "$o5 ", "$o7 ",]

    def flag_register_to_human(self, val=None):
        # http://www.gaisler.com/doc/sparcv8.pdf
        reg = self.flag_register
        if not val:
            val = get_register_ex(reg)
        return flags_to_human(val, self.flags_table)

    def is_call(self, insn):
        return False

    def is_conditional_branch(self, insn):
        mnemo = insn.mnemo
        # http://moss.csc.ncsu.edu/~mueller/codeopt/codeopt00/notes/condbranch.html
        branch_mnemos = {
            "be", "bne", "bg", "bge", "bgeu", "bgu", "bl", "ble", "blu", "bleu",
            "bneg", "bpos", "bvs", "bvc", "bcs", "bcc"
        }
        return mnemo in branch_mnemos

    def is_branch_taken(self, insn):
        mnemo = insn.mnemo
        flags = dict((self.flags_table[k], k) for k in self.flags_table)
        val = get_register_ex(self.flag_register)
        taken, reason = False, ""

        if mnemo == "be": taken, reason = val&(1<<flags["zero"]), "Z"
        elif mnemo == "bne": taken, reason = val&(1<<flags["zero"]) == 0, "!Z"
        elif mnemo == "bg": taken, reason = val&(1<<flags["zero"]) == 0 and (val&(1<<flags["negative"]) == 0 or val&(1<<flags["overflow"]) == 0), "!Z && (!N || !O)"
        elif mnemo == "bge": taken, reason = val&(1<<flags["negative"]) == 0 or val&(1<<flags["overflow"]) == 0, "!N || !O"
        elif mnemo == "bgu": taken, reason = val&(1<<flags["carry"]) == 0 and val&(1<<flags["zero"]) == 0, "!C && !Z"
        elif mnemo == "bgeu": taken, reason = val&(1<<flags["carry"]) == 0, "!C"
        elif mnemo == "bl": taken, reason = val&(1<<flags["negative"]) and val&(1<<flags["overflow"]), "N && O"
        elif mnemo == "blu": taken, reason = val&(1<<flags["carry"]), "C"
        elif mnemo == "ble": taken, reason = val&(1<<flags["zero"]) or (val&(1<<flags["negative"]) or val&(1<<flags["overflow"])), "Z || (N || O)"
        elif mnemo == "bleu": taken, reason = val&(1<<flags["carry"]) or val&(1<<flags["zero"]), "C || Z"
        elif mnemo == "bneg": taken, reason = val&(1<<flags["negative"]), "N"
        elif mnemo == "bpos": taken, reason = val&(1<<flags["negative"]) == 0, "!N"
        elif mnemo == "bvs": taken, reason = val&(1<<flags["overflow"]), "O"
        elif mnemo == "bvc": taken, reason = val&(1<<flags["overflow"]) == 0, "!O"
        elif mnemo == "bcs": taken, reason = val&(1<<flags["carry"]), "C"
        elif mnemo == "bcc": taken, reason = val&(1<<flags["carry"]) == 0, "!C"
        return taken, reason

    def mprotect_asm(self, addr, size, perm):
        hi = (addr & 0xffff0000) >> 16
        lo = (addr & 0x0000ffff)
        _NR_mprotect = 125
        syscall = "t 0x6d" if is_sparc64() else "t 0x10"
        insns = ["add %sp, -16, %sp",
                 "st %g1, [ %sp ]", "st %o0, [ %sp + 4 ]",
                 "st %o1, [ %sp + 8 ]", "st %o2, [ %sp + 12 ]",
                 "sethi  %hi({}), %o0".format(hi),
                 "or  %o0, {}, %o0".format(lo),
                 "clr  %o1",
                 "clr  %o2",
                 "mov  {}, %g1".format(_NR_mprotect),
                 syscall,
                 "ld [ %sp ], %g1", "ld [ %sp + 4 ], %o0",
                 "ld [ %sp + 8 ], %o1", "ld [ %sp + 12 ], %o2",
                 "add %sp, 16, %sp",]
        return "; ".join(insns)


class SPARC64(SPARC):
    """ Refs:
    - http://math-atlas.sourceforge.net/devel/assembly/abi_sysV_sparc.pdf
    - https://cr.yp.to/2005-590/sparcv9.pdf
    """
    arch = "SPARC"
    mode = "V9"

    all_registers = [
        "$g0   ", "$g1   ", "$g2   ", "$g3   ", "$g4   ", "$g5   ", "$g6   ", "$g7   ",
        "$o0   ", "$o1   ", "$o2   ", "$o3   ", "$o4   ", "$o5   ", "$o7   ",
        "$l0   ", "$l1   ", "$l2   ", "$l3   ", "$l4   ", "$l5   ", "$l6   ", "$l7   ",
        "$i0   ", "$i1   ", "$i2   ", "$i3   ", "$i4   ", "$i5   ", "$i7   ",
        "$pc   ", "$npc  ", "$sp   ", "$fp   ", "$state", ]

    flag_register = "$state" # sparcv9.pdf, 5.1.5.1 (ccr)
    flags_table = {
        35: "negative",
        34: "zero",
        33: "overflow",
        32: "carry",
    }



class MIPS(Architecture):
    arch = "MIPS"
    mode = "MIPS32"

    # http://vhouten.home.xs4all.nl/mipsel/r3000-isa.html
    all_registers = [
        "$zero     ", "$at       ", "$v0       ", "$v1       ", "$a0       ", "$a1       ", "$a2       ", "$a3       ",
        "$t0       ", "$t1       ", "$t2       ", "$t3       ", "$t4       ", "$t5       ", "$t6       ", "$t7       ",
        "$s0       ", "$s1       ", "$s2       ", "$s3       ", "$s4       ", "$s5       ", "$s6       ", "$s7       ",
        "$t8       ", "$t9       ", "$k0       ", "$k1       ", "$s8       ", "$status   ", "$badvaddr ", "$cause    ",
        "$pc       ", "$sp       ", "$hi       ", "$lo       ", "$fir      ", "$fcsr     ", "$ra       ", "$gp       ",]
    # https://en.wikipedia.org/wiki/MIPS_instruction_set
    nop_insn = b"\x00\x00\x00\x00" # sll $0,$0,0
    return_register = "$v0"
    flag_register = "$fcsr"
    flags_table = {}
    function_parameters = ["$a0", "$a1", "$a2", "$a3"]

    def flag_register_to_human(self, val=None):
        return Color.colorify("No flag register", attrs="yellow underline")

    def is_call(self, insn):
        return False

    def is_conditional_branch(self, insn):
        mnemo = insn.mnemo
        branch_mnemos = {"beq", "bne", "beqz", "bnez", "bgtz", "bgez", "bltz", "blez"}
        return mnemo in branch_mnemos

    def is_branch_taken(self, insn):
        mnemo, ops = insn.mnemo, insn.operands
        taken, reason = False, ""

        if mnemo == "beq":
            taken, reason = get_register_ex(ops[0]) == get_register_ex(ops[1]), "{0[0]} == {0[1]}".format(ops)
        elif mnemo == "bne":
            taken, reason = get_register_ex(ops[0]) != get_register_ex(ops[1]), "{0[0]} != {0[1]}".format(ops)
        elif mnemo == "beqz":
            taken, reason = get_register_ex(ops[0]) == 0, "{0[0]} == 0".format(ops)
        elif mnemo == "bnez":
            taken, reason = get_register_ex(ops[0]) != 0, "{0[0]} != 0".format(ops)
        elif mnemo == "bgtz":
            taken, reason = get_register_ex(ops[0]) > 0, "{0[0]} > 0".format(ops)
        elif mnemo == "bgez":
            taken, reason = get_register_ex(ops[0]) >= 0, "{0[0]} >= 0".format(ops)
        elif mnemo == "bltz":
            taken, reason = get_register_ex(ops[0]) < 0, "{0[0]} < 0".format(ops)
        elif mnemo == "blez":
            taken, reason = get_register_ex(ops[0]) <= 0, "{0[0]} <= 0".format(ops)
        return taken, reason

    def mprotect_asm(self, addr, size, perm):
        _NR_mprotect = 4125
        insns = ["addi $sp, $sp, -16",
                 "sw $v0, 0($sp)", "sw $a0, 4($sp)",
                 "sw $a3, 8($sp)", "sw $a3, 12($sp)",
                 "li $v0, {:d}".format(_NR_mprotect),
                 "li $a0, {:d}".format(addr),
                 "li $a1, {:d}".format(size),
                 "li $a2, {:d}".format(perm),
                 "syscall",
                 "lw $v0, 0($sp)", "lw $a1, 4($sp)",
                 "lw $a3, 8($sp)", "lw $a3, 12($sp)",
                 "addi $sp, $sp, 16",]
        return "; ".join(insns)


def write_memory(address, buffer, length=0x10):
    if PYTHON_MAJOR == 2: buffer = str(buffer)
    return gdb.selected_inferior().write_memory(address, buffer, length)


def read_memory(addr, length=0x10):
    if PYTHON_MAJOR == 2:
        return gdb.selected_inferior().read_memory(addr, length)

    return gdb.selected_inferior().read_memory(addr, length).tobytes()


def read_int_from_memory(addr):
    arch = get_memory_alignment()
    mem = read_memory(addr, arch)
    fmt = endian_str() + "I" if arch == 4 else endian_str() + "Q"
    return struct.unpack(fmt, mem)[0]


def read_cstring_from_memory(address):
    """
    Read a C-string from memory using GDB memory access.
    """
    char_ptr = gdb.lookup_type("char").pointer()
    res = gdb.Value(address).cast(char_ptr).string().strip()

    i = res.find("\n")
    if i != -1 and len(res) > get_memory_alignment():
        res = "{}[...]".format(res[:i])

    return res


def is_readable_string(address):
    """
    Here we will assume that a readable string is
    a consecutive byte array where:
    * the last element is 0x00 (i.e. it is a C-string)
    * each byte is printable
    """
    try:
        cstr = read_cstring_from_memory(address)
        return isinstance(cstr, unicode) and cstr and all([x in string.printable for x in cstr])
    except UnicodeDecodeError:
        return False


def is_alive():
    """Check if GDB is running."""
    try:
        return get_pid() > 0
    except gdb.error:
        return False
    return False


def if_gdb_running(f):
    """Decorator wrapper to check if GDB is running."""
    @functools.wraps(f)
    def wrapper(*args, **kwds):
        if is_alive():
            return f(*args, **kwds)
        else:
            warn("No debugging session active")
    return wrapper


def is_linux_command(f):
    """Decorator wrapper to check if the command is run on a linux system."""
    @functools.wraps(f)
    def wrapper(*args, **kwds):
        if sys.platform.startswith("linux"):
            return f(*args, **kwds)
        else:
            warn("This command only runs on Linux")
    return wrapper


def to_unsigned_long(v):
    unsigned_long_t = gdb.lookup_type("unsigned long")
    return long(v.cast(unsigned_long_t))


def get_register(regname):
    """
    Get register value. Exception will be raised if expression cannot be parse.
    This function won't catch on purpose.
    @param regname: expected register
    @return register value
    """
    return get_register_ex(regname.strip())


def get_register_ex(regname):
    try:
        value = gdb.parse_and_eval(regname)
        return long(value)
    except gdb.error:
        value = gdb.selected_frame().read_register(regname)
        return long(value)


@lru_cache()
def get_os():
    return platform.system().lower()


@lru_cache()
def get_pid():
    return get_frame().pid


def get_filepath():
    filename = gdb.current_progspace().filename

    if is_remote_debug():
        # if no filename specified, try downloading target from /proc
        if filename is None:
            pid = get_pid()
            if pid > 0:
                return download_file("/proc/{:d}/exe".format(pid), use_cache=True)
            else:
                return None

        # if target is remote file, download
        elif filename.startswith("target:"):
            return download_file(filename[len("target:"):], use_cache=True)
        else:
            return filename
    else:
        return filename


def download_file(target, use_cache=False):
    """Download filename `target` inside the mirror tree in /tmp"""
    try:
        local_root = GEF_TEMP_DIR
        local_path = os.path.join(local_root, os.path.dirname(target))
        local_name = os.path.join(local_path, os.path.basename(target))
        if use_cache and os.path.isfile(local_name):
            return local_name
        gef_makedirs(local_path)
        gdb.execute("remote get {0:s} {1:s}".format(target, local_name))
    except Exception as e:
        err(str(e))
        local_name = None
    return local_name


def open_file(path, use_cache=False):
    """Attempt to open the given file, if remote debugging is active, download
    it first to the mirror in /tmp/"""
    if is_remote_debug():
        lpath = download_file(path, use_cache)
        if not lpath:
            raise IOError("cannot open remote path {:s}".format(path))
        return open(lpath)
    else:
        return open(path)


def get_filename():
    return os.path.basename(get_filepath())


def get_function_length(sym):
    """Attempt to get the length of the raw bytes of a function."""
    dis = gdb.execute("disassemble {:s}".format(sym), to_string=True).splitlines()
    start_addr = int(dis[1].split()[0], 16)
    end_addr = int(dis[-2].split()[0], 16)
    return end_addr - start_addr


def command_only_works_for(os):
    """Use this command in the `pre_load()`, to filter the Operating Systems this
    command is working on."""
    curos = get_os()
    if not any(filter(lambda x: x == curos, os)):
        raise GefUnsupportedOS("This command only works for {:s}".format(", ".join(os)))
    return


def __get_process_maps_linux(proc_map_file):
    f = open_file(proc_map_file, use_cache=False)
    for line in f:
        line = line.strip()

        addr, perm, off, _, rest = line.split(" ", 4)
        rest = rest.split(" ", 1)
        if len(rest) == 1:
            inode = rest[0]
            pathname = ""
        else:
            inode = rest[0]
            pathname = rest[1].replace(" ", "")

        addr_start, addr_end = addr.split("-")
        addr_start, addr_end = long(addr_start, 16), long(addr_end, 16)
        off = long(off, 16)
        perm = Permission.from_process_maps(perm)

        yield Section(page_start=addr_start,
                      page_end=addr_end,
                      offset=off,
                      permission=perm,
                      inode=inode,
                      path=pathname)

    return


def __get_process_maps_freebsd(proc_map_file):
    f = open_file(proc_map_file, use_cache=False)
    for line in f:
        line = line.strip()

        start_addr, end_addr, _, _, _, perm, _, _, _, _, _, inode, pathname, _, _ = line.split()
        start_addr, end_addr = long(start_addr, 0x10), long(end_addr, 0x10)
        offset = 0
        perm = Permission.from_process_maps(perm)

        yield Section(page_start=start_addr,
                      page_end=end_addr,
                      offset=offset,
                      permission=perm,
                      inode=inode,
                      path=pathname)

    return


@lru_cache()
def get_process_maps():
    try:
        pid = get_pid()

        if sys.platform.startswith("linux"):
            sections = __get_process_maps_linux("/proc/{:d}/maps".format(pid))
        elif sys.platform.startswith("freebsd"):
            sections = __get_process_maps_freebsd("/proc/{:d}/map".format(pid))
        else:
            sections = []
    except Exception:
        warn("Failed to read /proc/<PID>/maps, using GDB sections info")
        sections = get_info_sections()

    return list(sections)


@lru_cache()
def get_info_sections():
    stream = StringIO(gdb.execute("maintenance info sections", to_string=True))

    for line in stream:
        if not line:
            break

        try:
            parts = [x.strip() for x in line.split()]
            addr_start, addr_end = [long(x, 16) for x in parts[1].split("->")]
            off = long(parts[3][:-1], 16)
            path = parts[4]
            inode = ""
            perm = Permission.from_info_sections(parts[5:])

            yield Section(page_start=addr_start,
                          page_end=addr_end,
                          offset=off,
                          permission=perm,
                          inode=inode,
                          path=path)

        except IndexError:
            continue
        except ValueError:
            continue

    return


def get_info_files():
    lines = gdb.execute("info files", to_string=True).splitlines()

    if len(lines) < len(__infos_files__):
        return __infos_files__

    for line in lines:
        line = line.strip().rstrip()

        if not line:
            break

        if not line.startswith("0x"):
            continue

        blobs = [x.strip() for x in line.split(" ")]
        addr_start = long(blobs[0], 16)
        addr_end = long(blobs[2], 16)
        section_name = blobs[4]

        if len(blobs) == 7:
            filename = blobs[6]
        else:
            filename = get_filepath()

        info = Zone()
        info.name = section_name
        info.zone_start = addr_start
        info.zone_end = addr_end
        info.filename = filename

        __infos_files__.append(info)

    return __infos_files__


def process_lookup_address(address):
    if not is_alive():
        err("Process is not running")
        return None

    if is_x86_64() or is_x86_32() :
        if is_in_x86_kernel(address):
            return None

    for sect in get_process_maps():
        if sect.page_start <= address < sect.page_end:
            return sect

    return None


def process_lookup_path(name, perm=Permission.ALL):
    if not is_alive():
        err("Process is not running")
        return None

    for sect in get_process_maps():
        if name in sect.path and sect.permission.value & perm:
            return sect

    return None


def file_lookup_address(address):
    for info in get_info_files():
        if info.zone_start <= address < info.zone_end:
            return info
    return None


def lookup_address(address):
    """Tries to find the address in the process address space.
    Return an Address object, with validity flag set based on success."""
    sect = process_lookup_address(address)
    info = file_lookup_address(address)
    if sect is None and info is None:
        # i.e. there is no info on this address
        return Address(value=address, valid=False)
    else:
        return Address(value=address, section=sect, info=info)


def xor(data, key):
    key = key.lstrip("0x")
    key = binascii.unhexlify(key)
    if PYTHON_MAJOR == 2:
        return b"".join([chr(ord(x) ^ ord(y)) for x, y in zip(data, itertools.cycle(key))])

    return bytearray([x ^ y for x, y in zip(data, itertools.cycle(key))])


def ishex(pattern):
    if pattern.startswith("0x") or pattern.startswith("0X"):
        pattern = pattern[2:]
    return all(c in string.hexdigits for c in pattern)


def ida_synchronize_handler(event):
    gdb.execute("ida-interact Sync", from_tty=True, to_string=True)
    return


def continue_handler(event):
    return


def hook_stop_handler(event):
    gdb.execute("context")
    return


def new_objfile_handler(event):
    set_arch()
    reset_all_caches()
    return


def exit_handler(event):
    reset_all_caches()
    return


def get_terminal_size():
    """
    Portable function to retrieve the current terminal size.
    """
    if is_debug():
        return 600, 100

    cmd = struct.unpack("hh", fcntl.ioctl(1, termios.TIOCGWINSZ, "1234"))
    tty_rows, tty_columns = int(cmd[0]), int(cmd[1])
    return tty_rows, tty_columns


def get_generic_arch(module, prefix, arch, mode, big_endian, to_string=False):
    """
    Retrieves architecture and mode from the arguments for use for the holy
    {cap,key}stone/unicorn trinity.
    """
    if to_string:
        arch = "{:s}.{:s}_ARCH_{:s}".format(module.__name__, prefix, arch)
        if mode:
            mode = "{:s}.{:s}_MODE_{:s}".format(module.__name__, prefix, str(mode))
        else:
            mode = ""
        if is_big_endian():
            mode += " + {:s}.{:s}_MODE_BIG_ENDIAN".format(module.__name__, prefix)
        else:
            mode += " + {:s}.{:s}_MODE_LITTLE_ENDIAN".format(module.__name__, prefix)

    else:
        arch = getattr(module, "{:s}_ARCH_{:s}".format(prefix, arch))
        if mode:
            mode = getattr(module, "{:s}_MODE_{:s}".format(prefix, mode))
        else:
            mode = ""
        if big_endian:
            mode += getattr(module, "{:s}_MODE_BIG_ENDIAN".format(prefix))
        else:
            mode += getattr(module, "{:s}_MODE_LITTLE_ENDIAN".format(prefix))

    return arch, mode


def get_generic_running_arch(module, prefix, to_string=False):
    """
    Retrieves architecture and mode from the current context.
    """

    if not is_alive():
        return None, None

    if current_arch is not None:
        arch, mode = current_arch.arch, current_arch.mode
    else:
        raise GefUnsupportedOS("Emulation not supported for your OS")

    return get_generic_arch(module, prefix, arch, mode, is_big_endian(), to_string)


def get_unicorn_arch(arch=None, mode=None, endian=None, to_string=False):
    unicorn = sys.modules["unicorn"]
    if (arch, mode, endian) == (None,None,None):
        return get_generic_running_arch(unicorn, "UC", to_string)
    return get_generic_arch(unicorn, "UC", arch, mode, endian, to_string)


def get_capstone_arch(arch=None, mode=None, endian=None, to_string=False):
    capstone = sys.modules["capstone"]

    # hacky patch to unify capstone/ppc syntax with keystone & unicorn:
    # CS_MODE_PPC32 does not exist (but UC_MODE_32 & KS_MODE_32 do)
    if is_alive() and (is_powerpc() or is_ppc64()):
        if is_ppc64():
            raise GefUnsupportedOS("Capstone not supported for PPC64 yet.")

        arch = "PPC"
        mode = "32"
        endian = is_big_endian()
        return get_generic_arch(capstone, "CS", arch, mode, endian, to_string)

    if (arch, mode, endian) == (None,None,None):
        return get_generic_running_arch(capstone, "CS", to_string)
    return get_generic_arch(capstone, "CS", arch, mode, endian, to_string)


def get_keystone_arch(arch=None, mode=None, endian=None, to_string=False):
    keystone = sys.modules["keystone"]
    if (arch, mode, endian) == (None,None,None):
        return get_generic_running_arch(keystone, "KS", to_string)
    return get_generic_arch(keystone, "KS", arch, mode, endian, to_string)


def get_unicorn_registers(to_string=False):
    "Returns a dict matching the Unicorn identifier for a specific register."
    unicorn = sys.modules["unicorn"]
    regs = {}

    if current_arch is not None:
        arch = current_arch.arch.lower()
    else:
        raise GefUnsupportedOS("Oops")

    const = getattr(unicorn, "{}_const".format(arch))
    for reg in current_arch.all_registers:
        regname = "UC_{:s}_REG_{:s}".format(arch.upper(), reg.strip()[1:].upper())
        if to_string:
            regs[reg] = "{:s}.{:s}".format(const.__name__, regname)
        else:
            regs[reg] = getattr(const, regname)
    return regs


def keystone_assemble(code, arch, mode, *args, **kwargs):
    """Assembly encoding function based on keystone."""
    keystone = sys.modules["keystone"]
    code = gef_pybytes(code)
    addr = kwargs.get("addr", 0x1000)

    try:
        ks = keystone.Ks(arch, mode)
        enc = ks.asm(code, addr)[0]
    except keystone.KsError as e:
        err("Keystone assembler error: {:s}".format(e))
        return None

    enc = bytearray(enc)
    if "raw" not in kwargs:
        # print as string
        s = binascii.hexlify(enc)
        enc = b"\\x" + b"\\x".join([s[i:i + 2] for i in range(0, len(s), 2)])
        enc = enc.decode("utf-8")

    return enc


@lru_cache()
def get_elf_headers(filename=None):
    if filename is None:
        filename = get_filepath()

    if filename.startswith("target:"):
        warn("Your file is remote, you should try using `gef-remote` instead")
        return

    return Elf(filename)


@lru_cache()
def is_elf64(filename=None):
    elf = get_elf_headers(filename)
    return elf.e_class == Elf.ELF_64_BITS


@lru_cache()
def is_elf32(filename=None):
    elf = get_elf_headers(filename)
    return elf.e_class == Elf.ELF_32_BITS


@lru_cache()
def is_x86_64(filename=None):
    elf = get_elf_headers(filename)
    return elf.e_machine == Elf.X86_64


@lru_cache()
def is_x86_32(filename=None):
    elf = get_elf_headers(filename)
    return elf.e_machine == Elf.X86_32


@lru_cache()
def is_arm(filename=None):
    elf = get_elf_headers(filename)
    return elf.e_machine == Elf.ARM


@lru_cache()
def is_arm_thumb():
    # http://www.botskool.com/user-pages/tutorials/electronics/arm-7-tutorial-part-1
    return is_arm() and get_register("$cpsr") & (1<<5)


@lru_cache()
def is_mips():
    elf = get_elf_headers()
    return elf.e_machine == Elf.MIPS


@lru_cache()
def is_powerpc():
    elf = get_elf_headers()
    return elf.e_machine == Elf.POWERPC


@lru_cache()
def is_ppc64():
    elf = get_elf_headers()
    return elf.e_machine == Elf.POWERPC64


@lru_cache()
def is_sparc():
    elf = get_elf_headers()
    return elf.e_machine == Elf.SPARC


@lru_cache()
def is_sparc64():
    elf = get_elf_headers()
    return elf.e_machine == Elf.SPARC64


@lru_cache()
def is_aarch64():
    elf = get_elf_headers()
    return elf.e_machine == Elf.AARCH64


current_arch = None


def set_arch():
    global current_arch

    elf = get_elf_headers()

    if   elf.e_machine == Elf.ARM:        current_arch = ARM()
    elif elf.e_machine == Elf.AARCH64:    current_arch = AARCH64()
    elif elf.e_machine == Elf.X86_32:     current_arch = X86()
    elif elf.e_machine == Elf.X86_64:     current_arch = X86_64()
    elif elf.e_machine == Elf.POWERPC:    current_arch = PowerPC()
    elif elf.e_machine == Elf.POWERPC64:  current_arch = PowerPC64()
    elif elf.e_machine == Elf.SPARC:      current_arch = SPARC()
    elif elf.e_machine == Elf.SPARC64:    current_arch = SPARC64()
    elif elf.e_machine == Elf.MIPS:       current_arch = MIPS()
    else:
        raise GefUnsupportedOS("CPU type is currently not supported: {:s}".format(get_arch()))
    return


def get_memory_alignment(in_bits=False):
    if is_elf32():
        return 4 if not in_bits else 32
    elif is_elf64():
        return 8 if not in_bits else 64

    raise GefUnsupportedMode("GEF is running under an unsupported mode")


def clear_screen(tty=""):
    if not tty:
        gdb.execute("shell clear")
        return

    with open(tty, "w") as f:
        f.write("\x1b[H\x1b[J")
    return


def format_address(addr):
    memalign_size = get_memory_alignment()
    if memalign_size == 4:
        return "0x{:08x}".format(addr & 0xFFFFFFFF)

    return "0x{:016x}".format(addr & 0xFFFFFFFFFFFFFFFF)


def align_address(address):
    if get_memory_alignment(in_bits=True) == 32:
        ret = address & 0x00000000FFFFFFFF
    else:
        ret = address & 0xFFFFFFFFFFFFFFFF
    return ret


def align_address_to_page(address):
    a = align_address(address) >> DEFAULT_PAGE_ALIGN_SHIFT
    return a << DEFAULT_PAGE_ALIGN_SHIFT


def parse_address(address):
    if ishex(address):
        return long(address, 16)

    _type = gdb.lookup_type("unsigned long")
    _addr = gdb.parse_and_eval(address).cast(_type)
    return long(_addr)


def is_in_x86_kernel(address):
    address = align_address(address)
    memalign = get_memory_alignment(in_bits=True) - 1
    return (address >> memalign) == 0xF


@lru_cache()
def endian_str():
    elf = get_elf_headers()
    return "<" if elf.e_endianness == Elf.LITTLE_ENDIAN else ">"


@lru_cache()
def is_remote_debug():
    return "remote" in gdb.execute("maintenance print target-stack", to_string=True)


def de_bruijn(alphabet, n):
    """
    De Bruijn sequence for alphabet and subsequences of length n (for compat. w/ pwnlib)
    Source: https://github.com/Gallopsled/pwntools/blob/master/pwnlib/util/cyclic.py#L38
    """
    k = len(alphabet)
    a = [0] * k * n
    def db(t, p):
        if t > n:
            if n % p == 0:
                for j in range(1, p + 1):
                    yield alphabet[a[j]]
        else:
            a[t] = a[t - p]
            for c in db(t + 1, p):
                yield c

            for j in range(a[t - p] + 1, k):
                a[t] = j
                for c in db(t + 1, t):
                    yield c

    return db(1,1)


def generate_cyclic_pattern(length):
    """
    Create a cyclic pattern based on de Bruijn sequence.
    """
    charset = b"""abcdefghijklmnopqrstuvwxyz"""
    cycle = get_memory_alignment() if is_alive() else 4
    i = 0
    res = []

    for c in de_bruijn(charset, cycle):
        if i == length: break
        res.append(c)
        i += 1

    return bytearray(res)


def dereference(addr):
    """
    gef-wrapper for gdb dereference function.
    """
    try:
        unsigned_long_type = gdb.lookup_type("unsigned long").pointer()
        ret = gdb.Value(addr).cast(unsigned_long_type).dereference()
    except gdb.MemoryError:
        ret = None
    return ret


def gef_convenience(value):
    global __gef_convenience_vars_index__
    var_name = "$_gef{:d}".format(__gef_convenience_vars_index__)
    __gef_convenience_vars_index__ += 1
    gdb.execute("""set {:s} = {:s} """.format(var_name, value))
    return var_name


#
# Breakpoints
#
class FormatStringBreakpoint(gdb.Breakpoint):
    """Inspect stack for format string"""
    def __init__(self, spec, num_args):
        super(FormatStringBreakpoint, self).__init__(spec, type=gdb.BP_BREAKPOINT, internal=False)
        self.num_args = num_args
        self.enabled = True
        return

    def stop(self):

        if is_x86_32():
            sp = current_arch.sp
            m = get_memory_alignment()
            val = sp + (self.num_args * m) + m
            ptr = read_int_from_memory(val)
            addr = lookup_address(ptr)
            ptr = hex(ptr)
        else:
            regs = current_arch.function_parameters
            ptr = regs[self.num_args]
            addr = lookup_address(get_register_ex(ptr))

        if not addr.valid:
            return False

        if addr.section.permission.value & Permission.WRITE:
            content = read_cstring_from_memory(addr.value)

            print(titlify("Format String Detection"))
            m = "Possible insecure format string '{:s}' {:s} {:#x}: '{:s}'\n".format(ptr, right_arrow, addr.value, content)
            m += "Triggered by '{:s}()'".format(self.location)
            info(m)

            name = addr.info.name if addr.info else addr.section.path
            m = "Reason:\n"
            m += "Call to '{:s}()' with format string argument in position #{:d} is in ".format(self.location, self.num_args)
            m += "page {:#x} ({:s}) that has write permission".format(addr.section.page_start, name)
            warn(m)
            return True

        return False


class StubBreakpoint(gdb.Breakpoint):
    """Create a breakpoint to permanently disable a call (fork/alarm/signal/etc.)"""

    def __init__(self, func, retval):
        super(StubBreakpoint, self).__init__(func, gdb.BP_BREAKPOINT, internal=False)
        self.func = func
        self.retval = retval

        m = "All calls to '{:s}' will be skipped".format(self.func)
        if self.retval is not None:
            m += " (with return value set to {:#x})".format(self.retval)
        info(m)
        return

    def stop(self):
        retreg  = current_arch.return_register

        m = ["Ignoring call to '{:s}'".format(self.func)]
        cmd = "set {:s} = {:#x}".format(retreg, self.retval)
        m.append("(setting {:s} to {:#x})".format(retreg, self.retval))
        gdb.execute(cmd)

        gdb.execute("return")

        ok(" ".join(m))
        return False  # never stop at this breakpoint


class ChangePermissionBreakpoint(gdb.Breakpoint):
    """When hit, this temporary breakpoint will restore the original code, and position
    $pc correctly."""

    def __init__(self, loc, code, pc):
        super(ChangePermissionBreakpoint, self).__init__(loc, gdb.BP_BREAKPOINT, internal=False)
        self.original_code = code
        self.original_pc = pc
        return

    def stop(self):
        info("Restoring original context")
        write_memory(self.original_pc, self.original_code, len(self.original_code))
        info("Restoring $pc")
        gdb.execute("set $pc = {:#x}".format(self.original_pc))
        return True


#
# Commands
#

class GenericCommand(gdb.Command):
    """This is a meta-class for invoking commands, should not be invoked"""
    __metaclass__ = abc.ABCMeta

    def __init__(self, *args, **kwargs):
        self.pre_load()
        self.__doc__  += "\nSyntax: {}".format(self._syntax_)
        command_type = kwargs.setdefault("command", gdb.COMMAND_OBSCURE)
        complete_type = kwargs.setdefault("complete", gdb.COMPLETE_NONE)
        prefix = kwargs.setdefault("prefix", True)
        super(GenericCommand, self).__init__(self._cmdline_, command_type, complete_type, prefix)
        self.post_load()
        return

    def invoke(self, args, from_tty):
        argv = gdb.string_to_argv(args)
        self.do_invoke(argv)
        return

    def usage(self):
        err("Syntax\n{}".format(self._syntax_))
        return

    @abc.abstractproperty
    def _cmdline_(self): pass

    @abc.abstractproperty
    def _syntax_(self): pass

    @abc.abstractmethod
    def do_invoke(self, argv): pass

    def pre_load(self): pass
    def post_load(self): pass

    @property
    def settings(self): pass

    @settings.getter
    def settings(self):
        return { x.split(".", 1)[1]: __config__[x] for x in __config__
                 if x.startswith("{:s}.".format(self._cmdline_)) }

    def get_setting(self, name): return self.settings[name][1](self.settings[name][0])
    def has_setting(self, name): return name in self.settings

    def add_setting(self, name, value, description=""):
        key = "{:s}.{:s}".format(self.__class__._cmdline_, name)
        __config__[key] = [value, type(value), description]
        return

    def del_setting(self, name):
        key = "{:s}.{:s}".format(self.__class__._cmdline_, name)
        __config__.pop(key)
        return


# Copy/paste this template for new command
# class TemplateCommand(GenericCommand):
# """TemplateCommand: description here will be seen in the help menu for the command."""

    # _cmdline_ = "template-fake"
    # _syntax_  = "{:s}".format(_cmdline_)
    # _aliases_ = ["tpl-fk",]
    # def __init__(self):
    #     super(TemplateCommand, self).__init__(complete=gdb.COMPLETE_FILENAME)
    #     return
    # def do_invoke(self, argv):
    #     return

class CanaryCommand(GenericCommand):
    """Shows the canary value of the current process. Apply the techique detailed in
    https://www.elttam.com.au/blog/playing-with-canaries/ to show the canary."""

    _cmdline_ = "canary"
    _syntax_  = "{:s}".format(_cmdline_)

    @if_gdb_running
    def do_invoke(self, argv):
        self.dont_repeat()

        has_canary = checksec(get_filepath())["Canary"]
        if not has_canary:
            warn("This binary was not compiled with SSP.")
            return

        for line in gdb.execute("info auxv", to_string=True).splitlines():
            tmp = line.split()
            _type, _addr = tmp[1], tmp[-1]
            if _type != "AT_RANDOM":
                continue
            _addr = int(_addr, 16)
            nb = get_memory_alignment()
            info("Found AT_RANDOM at {:#x}, reading {} bytes".format(_addr, nb))
            canary = read_int_from_memory(_addr)
            canary &= ~0xff
            info("The canary of process {} is {:#x}".format(get_pid(), canary))
        return


class ProcessStatusCommand(GenericCommand):
    """Extends the info given by GDB `info proc`, by giving an exhaustive description of the
    process status (file descriptors, ancestor, descendants, etc.). """

    _cmdline_ = "process-status"
    _syntax_  = "{:s}".format(_cmdline_)
    _aliases_ = ["status", ]

    def __init__(self):
        super(ProcessStatusCommand, self).__init__(complete=gdb.COMPLETE_NONE, prefix=False)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if is_remote_debug():
            warn("'{:s}' cannot be used while remote debugging".format(self._cmdline_))
            return

        self.show_info_proc()
        self.show_ancestor()
        self.show_descendants()
        self.show_fds()
        self.show_connections()
        return

    def get_state_of(self, pid):
        res = {}
        for line in open("/proc/{}/status".format(pid), "r"):
            key, value = line.split(":", 1)
            res[key.strip()] = value.strip()
        return res

    def get_cmdline_of(self, pid):
        return open("/proc/{}/cmdline".format(pid), "r").read().replace("\x00", "\x20").strip()

    def get_process_path_of(self, pid):
        return os.readlink("/proc/{}/exe".format(pid))

    def get_children_pids(self, pid):
        ps = which("ps")
        cmd = [ps, "-o", "pid", "--ppid","{}".format(pid), "--noheaders"]
        try:
            return gef_execute_external(cmd, as_list=True)
        except Exception:
            return []

    def show_info_proc(self):
        info("Process Information")
        pid = get_pid()
        cmdline = self.get_cmdline_of(pid)
        print("\tPID {} {}".format(right_arrow, pid))
        print("\tExecutable {} {}".format(right_arrow, self.get_process_path_of(pid)))
        print("\tCommand line {} '{}'".format(right_arrow, cmdline))
        return

    def show_ancestor(self):
        info("Parent Process Information")
        ppid = int(self.get_state_of(get_pid())["PPid"])
        state = self.get_state_of(ppid)
        cmdline = self.get_cmdline_of(ppid)
        print("\tParent PID {} {}".format(right_arrow, state["Pid"]))
        print("\tCommand line {} '{}'".format(right_arrow, cmdline))
        return

    def show_descendants(self):
        info("Children Process Information")
        children = self.get_children_pids(get_pid())
        if len(children)==0:
            print("\tNo child process")
            return

        for child_pid in children:
            state = self.get_state_of(child_pid)
            pid = state["Pid"]
            print("\tPID {} {} (Name: '{}', CmdLine: '{}')".format(right_arrow,
                                                                   pid,
                                                                   self.get_process_path_of(pid),
                                                                   self.get_cmdline_of(pid)))
            return

    def show_fds(self):
        pid = get_pid()
        path = "/proc/{:d}/fd".format(pid)

        info("File Descriptors:")
        for fname in os.listdir(path):
            fullpath = os.path.join(path, fname)
            if os.path.islink(fullpath):
                print("\t{:s} {:s} {:s}".format (fullpath, right_arrow, os.readlink(fullpath)))
        return

    def list_sockets(self, pid):
        sockets = []
        path = "/proc/{:d}/fd".format(pid)
        for fname in os.listdir(path):
            fullpath = os.path.join(path, fname)
            if os.path.islink(fullpath) and os.readlink(fullpath).startswith("socket:"):
                p = os.readlink(fullpath).replace("socket:", "")[1:-1]
                sockets.append(int(p))
        return sockets

    def parse_ip_port(self, addr):
        ip, port = addr.split(":")
        return socket.inet_ntoa(struct.pack("<I", int(ip, 16))), int(port, 16)

    def show_connections(self):
        # https://github.com/torvalds/linux/blob/v4.7/include/net/tcp_states.h#L16
        tcp_states_str = {
            0x01: "TCP_ESTABLISHED",
            0x02: "TCP_SYN_SENT",
            0x03: "TCP_SYN_RECV",
            0x04: "TCP_FIN_WAIT1",
            0x05: "TCP_FIN_WAIT2",
            0x06: "TCP_TIME_WAIT",
            0x07: "TCP_CLOSE",
            0x08: "TCP_CLOSE_WAIT",
            0x09: "TCP_LAST_ACK",
            0x0a: "TCP_LISTEN",
            0x0b: "TCP_CLOSING",
            0x0c: "TCP_NEW_SYN_RECV",
        }

        udp_states_str = {
            0x07: "UDP_LISTEN",
        }

        info("Network Connections")
        pid = get_pid()
        sockets = self.list_sockets(pid)
        if len(sockets)==0:
            print("\tNo open connections")
            return

        entries = {}
        entries["TCP"] = [x.split() for x in open("/proc/{:d}/net/tcp".format(pid), "r").readlines()[1:]]
        entries["UDP"]= [x.split() for x in open("/proc/{:d}/net/udp".format(pid), "r").readlines()[1:]]

        for proto in entries:
            for entry in entries[proto]:
                local, remote, state = entry[1:4]
                inode = int(entry[9])
                if inode in sockets:
                    local = self.parse_ip_port(local)
                    remote = self.parse_ip_port(remote)
                    state = int(state, 16)
                    state_str = tcp_states_str[state] if proto=="TCP" else udp_states_str[state]

                    print("\t{}:{} {} {}:{} ({})".format(local[0], local[1],
                                                         right_arrow,
                                                         remote[0], remote[1],
                                                         state_str))
        return



class GefThemeCommand(GenericCommand):
    """Customize GEF appearance."""
    _cmdline_ = "theme"
    _syntax_  = "{:s} [KEY [VALUE]]".format(_cmdline_)

    def __init__(self, *args, **kwargs):
        super(GefThemeCommand, self).__init__(GefThemeCommand._cmdline_, prefix=False)
        self.add_setting("context_title_line", "green bold")
        self.add_setting("context_title_message", "red bold")
        self.add_setting("default_title_line", "green bold")
        self.add_setting("default_title_message", "red bold")
        self.add_setting("xinfo_title_message", "blue bold")
        self.add_setting("dereference_string", "green")
        self.add_setting("dereference_code", "red")
        self.add_setting("dereference_base_address", "bold green")
        self.add_setting("dereference_register_value", "bold green")
        self.add_setting("registers_register_name", "bold red")
        self.add_setting("disable_color", "0", "Disable all colors in GEF")
        # TODO: add more customizable items
        return

    def do_invoke(self, args):
        self.dont_repeat()
        argc = len(args)

        if argc==0:
            for item in self.settings:
                value = self.settings[item][0]
                value = Color.colorify(value, attrs=value)
                print("{:40s}: {:s}".format(item, value))
            return

        key = args[0]
        if not self.has_setting(key):
            err("Invalid key")
            return

        if argc==1:
            value = self.settings[key][0]
            value = Color.colorify(value, attrs=value)
            print("{:40s}: {:s}".format(key, value))
            return

        val = [x for x in args[1:] if x in Color.colors]
        self.add_setting(key, " ".join(val))
        return


class PCustomCommand(GenericCommand):
    """Dump user defined structure.
    This command attempts to reproduce WinDBG awesome `dt` command for GDB and allows
    to apply structures (from symbols or custom) directly to an address.
    Custom structures can be defined in pure Python using ctypes, and should be stored
    in a specific directory, whose path must be stored in the `pcustom.struct_path`
    configuration setting."""

    _cmdline_ = "pcustom"
    _syntax_  = "{:s} [-l] [StructA [0xADDRESS] [-e]]".format(_cmdline_)
    _aliases_ = ["dt",]

    def __init__(self):
        super(PCustomCommand, self).__init__(complete=gdb.COMPLETE_SYMBOL, prefix=False)
        self.add_setting("struct_path", os.path.join(GEF_TEMP_DIR, "structs"),
                         "Path to store/load the structure ctypes files")
        return

    def do_invoke(self, argv):
        argc = len(argv)
        if argc == 0:
            self.usage()
            return

        if argv[0] == "-l":
            self.list_custom_structures()
            return

        modname, structname = argv[0].split(":", 1) if ":" in argv[0] else (argv[0], argv[0])
        structname, _ = structname.split(".", 1) if "." in structname else (structname, None)

        if argc == 1:
            self.dump_structure(modname, structname)
            return

        if argv[1] == "-e":
            self.create_or_edit_structure(modname, structname)
            return

        if not is_alive():
            return

        try:
            address = long(gdb.parse_and_eval(argv[1]))
        except gdb.error:
            err("Failed to parse '{:s}'".format(argv[1]))
            return

        self.apply_structure_to_address(modname, structname, address)
        return

    def pcustom_filepath(self, x):
        return os.path.join(self.get_setting("struct_path"), "{}.py".format(x))

    def is_valid_struct(self, x):
        return os.access(self.pcustom_filepath(x), os.R_OK)

    def dump_structure(self, mod_name, struct_name):
        # If it's a builtin or defined in the ELF use gdb's `ptype`
        try:
            gdb.execute("ptype struct {:s}".format(struct_name))
            return
        except gdb.error:
            pass

        self.dump_custom_structure(mod_name, struct_name)
        return

    def dump_custom_structure(self, mod_name, struct_name):
        if not self.is_valid_struct(mod_name):
            err("Invalid structure name '{:s}'".format(struct_name))
            return

        _class = self.get_class(mod_name, struct_name)
        _offset = 0

        for _name, _type in _class._fields_:
            _size = ctypes.sizeof(_type)
            print("+{:04x} {:s} {:s} ({:#x})".format(_offset, _name, _type.__name__, _size))
            _offset += _size
        return

    def deserialize(self, struct, data):
        length = min(len(data), ctypes.sizeof(struct))
        ctypes.memmove(ctypes.addressof(struct), data, length)
        return

    def get_module(self, modname):
        _fullname = self.pcustom_filepath(modname)
        return imp.load_source(modname, _fullname)

    def get_class(self, modname, classname):
        _mod = self.get_module(modname)
        return getattr(_mod, classname)()

    def list_all_structs(self, modname):
        _mod = self.get_module(modname)
        _invalid = set(["BigEndianStructure", "LittleEndianStructure", "Structure"])
        _structs = set([x for x in dir(_mod) \
                         if inspect.isclass(getattr(_mod, x)) \
                         and issubclass(getattr(_mod, x), ctypes.Structure)])
        return _structs - _invalid

    def apply_structure_to_address(self, mod_name, struct_name, addr, depth=0):
        if not self.is_valid_struct(mod_name):
            err("Invalid structure name '{:s}'".format(struct_name))
            return

        _class = self.get_class(mod_name, struct_name)

        try:
            data = read_memory(addr, ctypes.sizeof(_class))
        except gdb.MemoryError:
            err("Cannot reach memory {:#x}".format(addr))
            return

        self.deserialize(_class, data)

        _regsize = get_memory_alignment()
        _offset = 0

        for field in _class._fields_:
            _name, _type = field
            _size = ctypes.sizeof(_type)
            _value = getattr(_class, _name)

            if    (_regsize == 4 and _type is ctypes.c_uint32) \
               or (_regsize == 8 and _type is ctypes.c_uint64) \
               or (_regsize == ctypes.sizeof(ctypes.c_void_p) and _type is ctypes.c_void_p):
                # try to dereference pointers
                _value = right_arrow.join(DereferenceCommand.dereference_from(_value))

            line = []
            line += "  "*depth
            line += ("{:#x}+0x{:04x} {} : ".format(addr, _offset, _name)).ljust(40)
            line += "{} ({})".format(_value, _type.__name__)
            parsed_value = self.get_ctypes_value(_class, _name, _value)
            if parsed_value:
                line += " {} {}".format(right_arrow, parsed_value)
            print("".join(line))

            if issubclass(_type, ctypes.Structure):
                self.apply_structure_to_address(mod_name, _type.__name__, addr + _offset, depth + 1)
                _offset += ctypes.sizeof(_type)
            else:
                _offset += _size
        return


    def get_ctypes_value(self, struct, item, value):
        if not hasattr(struct, "_values_"): return ""
        values_list = getattr(struct, "_values_")
        default = ""
        for name, values in values_list:
            if name != item: continue
            for val, desc in values:
                if value == val: return desc
                if val is None: default = desc
        return default


    def create_or_edit_structure(self, mod_name, struct_name):
        path = self.get_setting("struct_path")
        fullname = self.pcustom_filepath(mod_name)
        if not os.path.isdir(path):
            info("Creating path '{:s}'".format(path))
            gef_makedirs(path)
        elif not self.is_valid_struct(mod_name):
            info("Creating '{:s}' from template".format(fullname))
            with open(fullname, "wb") as f:
                f.write(self.get_template(struct_name))
                f.flush()
        else:
            info("Editing '{:s}'".format(fullname))

        cmd = os.getenv("EDITOR").split() if os.getenv("EDITOR") else ["nano",]
        cmd.append(fullname)
        retcode = subprocess.call(cmd)
        return retcode


    def get_template(self, structname):
        d = [
            b"from ctypes import *\n\n",
            b"class ",
            gef_pybytes(structname),
            b"(Structure):\n",
            b"    _fields_ = []\n"
        ]
        return b"".join(d)


    def list_custom_structures(self):
        path = self.get_setting("struct_path")
        info("Listing custom structures from '{:s}'".format(path))
        try:
            for filen in os.listdir(path):
                name, ext = os.path.splitext(filen)
                if ext != ".py": continue
                _modz = self.list_all_structs(name)
                ok("{:s} {:s} ({:s})".format(right_arrow, name, ", ".join(_modz)))
        except OSError:
            err("Cannot open '{:s}'.".format(path))
            warn("Create struct directory or use `gef config pcustom.struct_path` to set it correctly.")
        return


class RetDecCommand(GenericCommand):
    """Decompile code from GDB context using RetDec API."""

    _cmdline_ = "retdec"
    _syntax_  = "{:s} [-r RANGE1-RANGE2] [-s SYMBOL] [-a] [-h]".format(_cmdline_)
    _aliases_ = ["decompile",]

    def __init__(self):
        super(RetDecCommand, self).__init__(complete=gdb.COMPLETE_SYMBOL, prefix=False)
        self.add_setting("key", "", "RetDec decompilator API key")
        self.add_setting("path", GEF_TEMP_DIR, "Path to store the decompiled code")
        self.decompiler = None
        return

    def pre_load(self):
        try:
            __import__("retdec")
            __import__("retdec.decompiler")
        except ImportError:
            msg = "Missing Python `retdec-python` package. "
            raise GefMissingDependencyException(msg)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        arch = current_arch.arch.lower()
        if not arch:
            err("RetDec does not decompile '{:s}'".format(get_arch()))
            return

        api_key = self.get_setting("key").strip()
        if not api_key:
            warn("No RetDec API key provided, use `gef config` to add your own key")
            return

        if self.decompiler is None:
            retdec = sys.modules["retdec"]
            self.decompiler = retdec.decompiler.Decompiler(api_key=api_key)

        params = {
            "architecture": arch,
            "target_language": "c",
            "raw_endian": "big" if is_big_endian() else "little",
            "decomp_var_names": "readable",
            "decomp_emit_addresses": "no",
            "generate_cg": "no",
            "generate_cfg": "no",
            "comp_compiler": "gcc",
        }

        opts = getopt.getopt(argv, "r:s:ah")[0]
        if not opts:
            self.usage()
            return

        try:
            for opt, arg in opts:
                if opt == "-r":
                    range_from, range_to = map(lambda x: int(x,16), arg.split("-", 1))
                    fd, filename = tempfile.mkstemp()
                    with os.fdopen(fd, "wb") as f:
                        length = range_to - range_from
                        f.write(read_memory(range_from, length))
                    params["mode"] = "raw"
                    params["file_format"] = "elf"
                    params["raw_section_vma"] = hex(range_from)
                    params["raw_entry_point"] = hex(range_from)
                elif opt == "-s":
                    try:
                        value = gdb.parse_and_eval(arg)
                    except gdb.error:
                        err("No symbol named '{:s}'".format(arg))
                        return
                    range_from = long(value.address)
                    fd, filename = tempfile.mkstemp()
                    with os.fdopen(fd, "wb") as f:
                        f.write(read_memory(range_from, get_function_length(arg)))
                    params["mode"] = "raw"
                    params["file_format"] = "elf"
                    params["raw_section_vma"] = hex(range_from)
                    params["raw_entry_point"] = hex(range_from)
                elif opt == "-a":
                    filename = get_filepath()
                    params["mode"] = "bin"
                else:
                    self.usage()
                    return
        except Exception as excpt:
            err(excpt)
            return

        params["input_file"] = filename
        if self.send_to_retdec(params) == False:
            return

        fname = os.path.join(self.get_setting("path"), "{}.c".format(os.path.basename(filename)))
        with open(fname, "r") as f:
            pattern = re.compile(r"unknown_([a-f0-9]+)")
            for line in f:
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                # try to fix the unknown with the current context
                for match in pattern.finditer(line):
                    s = match.group(1)
                    pc = int(s, 16)
                    insn = gef_current_instruction(pc)
                    if insn.location:
                        line = line.replace("unknown_{:s}".format(s), insn.location)
                print(line)
        return


    def send_to_retdec(self, params):
        retdec = sys.modules["retdec"]

        try:
            path = self.get_setting("path")
            decompilation = self.decompiler.start_decompilation(**params)
            info("Task submitted, waiting for decompilation to finish... ", cr=False)
            decompilation.wait_until_finished()
            print("Done")
            decompilation.save_hll_code(self.get_setting("path"))
            fname = "{}/{}.{}".format(path, os.path.basename(params["input_file"]), params["target_language"])
            ok("Saved as '{:s}'".format(fname))
        except retdec.exceptions.AuthenticationError:
            err("Invalid RetDec API key")
            info("You can store your API key using `gef config`/`gef restore`")
            self.decompiler = None
            return False

        return True


class ChangeFdCommand(GenericCommand):
    """ChangeFdCommand: redirect file descriptor during runtime."""

    _cmdline_ = "hijack-fd"
    _syntax_  = "{:s} FD_NUM NEW_OUTPUT".format(_cmdline_)

    def __init__(self):
        super(ChangeFdCommand, self).__init__(prefix=False)
        return

    def do_invoke(self, argv):
        if not is_alive():
            err("No process alive")
            return

        if is_remote_debug():
            err("Cannot run on remote debugging")
            return

        if len(argv)!=2:
            self.usage()
            return

        if not os.access("/proc/{:d}/fd/{:s}".format(get_pid(), argv[0]), os.R_OK):
            self.usage()
            return

        old_fd = int(argv[0])
        new_output = argv[1]

        try:
            disable_context()
            res = gdb.execute("""call open("{:s}", 66, 0666)""".format(new_output), to_string=True)
            # Output example: $1 = 3
            new_fd = int(res.split()[2])
            info("Opened '{:s}' as fd=#{:d}".format(new_output, new_fd))
            gdb.execute("""call dup2({:d}, {:d})""".format(new_fd, old_fd), to_string=True)
            info("Duplicated FD #{:d} {:s} #{:d}".format(old_fd, right_arrow, new_fd))
            gdb.execute("""call close({:d})""".format(new_fd), to_string=True)
            ok("Success")
            enable_context()
        except Exception:
            err("Failed")
        return


class IdaInteractCommand(GenericCommand):
    """IDA Interact: set of commands to interact with IDA via a XML RPC service
    deployed via the IDA script `ida_gef.py`. It should be noted that this command
    can also be used to interact with Binary Ninja (using the script `binja_gef.py`)
    using the same interface."""

    _cmdline_ = "ida-interact"
    _syntax_  = "{:s} METHOD [ARGS]".format(_cmdline_)
    _aliases_ = ["binaryninja-interact", "bn", "binja"]

    def __init__(self):
        super(IdaInteractCommand, self).__init__(prefix=False)
        host, port = "127.0.1.1", 1337
        self.add_setting("host", host, "IP address to use connect to IDA/Binary Ninja script")
        self.add_setting("port", port, "Port to use connect to IDA/Binary Ninja script")
        self.sock = None
        self.version = ("", "")

        if self.is_target_alive(host, port):
            # if the target responds, we add 2 new handlers to synchronize the
            # info between gdb and ida/binja
            self.connect()
        return

    def is_target_alive(self, host, port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect((host, port))
            s.close()
        except socket.error:
            return False
        return True

    def connect(self, host=None, port=None):
        """
        Connect to the XML-RPC service.
        """
        if host is None:
            host = self.get_setting("host")
        if port is None:
            port = self.get_setting("port")

        try:
            sock = xmlrpclib.ServerProxy("http://{:s}:{:d}".format(host, port))
            gdb.events.stop.connect(ida_synchronize_handler)
            gdb.events.cont.connect(ida_synchronize_handler)
            self.version = sock.version()
        except Exception:
            err("Failed to connect to '{:s}:{:d}'".format(host, port))
            sock = None
        self.sock = sock
        return

    def disconnect(self):
        gdb.events.stop.disconnect(ida_synchronize_handler)
        gdb.events.cont.disconnect(ida_synchronize_handler)
        self.sock = None
        return

    def do_invoke(self, argv):
        def parsed_arglist(arglist):
            args = []
            for arg in arglist:
                try:
                    # try to solve the argument using gdb
                    argval = gdb.parse_and_eval(arg)
                    argval.fetch_lazy()
                    # check if value is addressable
                    argval = long(argval) if argval.address is None else long(argval.address)
                    args.append("{:#x}".format(argval,))
                except Exception:
                    # if gdb can't parse the value, let ida deal with it
                    args.append(arg)
            return args

        if self.sock is None:
            warn("Trying to reconnect")
            self.connect()
            if self.sock is None:
                self.disconnect()
                return

        if len(argv) == 0 or argv[0] in ("-h", "--help"):
            method_name = argv[1] if len(argv)>1 else None
            self.usage(method_name)
            return

        try:
            method_name = argv[0]
            if method_name == "version":
                self.version = self.sock.version()
                info("Enhancing {:s} with {:s} (v.{:s})".format(Color.greenify("gef"),
                                                                 Color.redify(self.version[0]),
                                                                 Color.yellowify(self.version[1])))
                return

            elif method_name == "Sync":
                self.synchronize()
                return

            method = getattr(self.sock, method_name)
            if len(argv) > 1:
                args = parsed_arglist(argv[1:])
                res = method(*args)
            else:
                res = method()

            if res in (0,  None):
                ok("Success")
                return

            if method_name in ("ImportStruct", "ImportStructs"):
                self.import_structures(res)
            else:
                print(res)

        except socket.error:
            self.disconnect()

        except Exception:
            err("[{:s}] Exception: {:s}".format(self._cmdline_, str(e)))
        return


    def synchronize(self):
        """Submit all active breakpoint addresses to IDA/BN"""
        breakpoints = gdb.breakpoints() or []
        old_bps = []

        for x in breakpoints:
            if x.enabled and not x.temporary:
                val = gdb.parse_and_eval(x.location)
                addr = str(val).strip().split()[0]
                addr = long(addr, 16)
                old_bps.append(addr)

        pc = current_arch.pc
        try:
            # it is possible that the server was stopped between now and the last sync
            cur_bps = self.sock.Sync(str(pc), old_bps)
        except ConnectionRefusedError:
            self.disconnect()
            return

        if cur_bps == old_bps:
            # no change
            return

        # add new BP defined in IDA/BN to gef
        added = set(cur_bps) - set(old_bps)
        for new_bp in added:
            gdb.Breakpoint("*{:#x}".format(new_bp), type=gdb.BP_BREAKPOINT)

        # and remove the old ones
        removed = set(old_bps) - set(cur_bps)
        for bp in breakpoints:
            val = gdb.parse_and_eval(bp.location).address
            addr = str(val).strip().split()[0]
            addr = long(addr, 16)
            if addr in removed:
                bp.delete()
        return


    def usage(self, meth=None):
        if self.sock is None:
            return

        if meth is not None:
            print(titlify(meth))
            print(self.sock.system.methodHelp(meth))
            return

        info("Listing available methods and syntax examples: ")
        for m in self.sock.system.listMethods():
            if m.startswith("system."): continue
            print(titlify(m))
            print(self.sock.system.methodHelp(m))
        return


    def import_structures(self, structs):
        if self.version[0] != "IDA Pro":
            return

        path = __config__.get("pcustom.struct_path")[0]
        if not os.path.isdir(path):
            gef_makedirs(path)

        for struct_name in structs:
            fullpath = os.path.join(path, "{}.py".format(struct_name))
            with open(fullpath, "wb") as f:
                f.write(b"from ctypes import *\n\n")
                f.write(b"class ")
                f.write(bytes(str(struct_name), encoding="utf-8"))
                f.write(b"(Structure):\n")
                f.write(b"    _fields_ = [\n")
                for _, name, size in structs[struct_name]:
                    name = bytes(name, encoding="utf-8")
                    if   size == 1: csize = b"c_uint8"
                    elif size == 2: csize = b"c_uint16"
                    elif size == 4: csize = b"c_uint32"
                    elif size == 8: csize = b"c_uint64"
                    else:           csize = b"c_byte * " + bytes(str(size), encoding="utf-8")
                    m = [b'        ("', name, b'", ', csize, b'),\n']
                    f.write(b"".join(m))
                f.write(b"]\n")
        ok("Success, {:d} structure{:s} imported".format(len(structs),
                                                         "s" if len(structs)>1 else ""))
        return


class SearchPatternCommand(GenericCommand):
    """SearchPatternCommand: search a pattern in memory."""

    _cmdline_ = "search-pattern"
    _syntax_  = "{:s} PATTERN".format(_cmdline_)
    _aliases_ = ["grep",]

    def __init__(self):
        super(SearchPatternCommand, self).__init__(prefix=False)
        return

    def search_pattern_by_address(self, pattern, start_address, end_address):
        """Search a pattern within a range defined by arguments."""
        pattern = gef_pybytes(pattern)
        length = end_address - start_address
        buf = read_memory(start_address, length)
        locations = []

        for m in re.finditer(pattern, buf):
            try:
                start = start_address + m.start()
                string = read_cstring_from_memory(start)
                end   = start + len(string)
            except UnicodeError:
                string = gef_pystring(pattern)+"[...]"
                end    = start + len(pattern)
            locations.append((start, end, string))
        return locations

    def search_pattern(self, pattern):
        """Search a pattern within the whole userland memory."""
        for section in get_process_maps():
            if not section.permission & Permission.READ: continue
            if section.path == "[vvar]": continue

            start = section.page_start
            end   = section.page_end - 1
            for loc in self.search_pattern_by_address(pattern, start, end):
                print("""{:#x} - {:#x} {}  "{}" """.format(loc[0], loc[1], right_arrow, Color.pinkify(loc[2])))
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if len(argv)!=1:
            self.usage()
            return

        pattern = argv[0]
        info("Searching '{:s}' in memory".format(Color.yellowify(pattern)))
        self.search_pattern(pattern)
        return


class FlagsCommand(GenericCommand):
    """Edit flags in a human friendly way"""

    _cmdline_ = "edit-flags"
    _syntax_  = "{:s} [(+|-|~)FLAGNAME ...]".format(_cmdline_)
    _aliases_ = ["flags",]

    def __init__(self):
        super(FlagsCommand, self).__init__(prefix=False)
        return

    def do_invoke(self, argv):
        for flag in argv:
            if len(flag)<2:
                continue

            action = flag[0]
            name = flag[1:].lower()

            if action not in ("+", "-", "~"):
                err("Invalid action for flag '{:s}'".format(flag))
                continue

            if name not in current_arch.flags_table.values():
                err("Invalid flag name '{:s}'".format(flag[1:]))
                continue

            for k in current_arch.flags_table:
                if current_arch.flags_table[k] == name:
                    off = k
                    break

            old_flag = get_register_ex(current_arch.flag_register)
            if action == "+":
                new_flags = old_flag | (1 << off)
            elif action == "-":
                new_flags = old_flag & ~(1 << off)
            else:
                new_flags = old_flag ^ (1<<off)

            gdb.execute("set ({:s}) = {:#x}".format(current_arch.flag_register, new_flags))

        print(current_arch.flag_register_to_human())
        return


class ChangePermissionCommand(GenericCommand):
    """Change a page permission. By default, it will change it to RWX."""

    _cmdline_ = "set-permission"
    _syntax_  = "{:s} LOCATION [PERMISSION]".format(_cmdline_)
    _aliases_ = ["mprotect",]

    def __init__(self):
        super(ChangePermissionCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        return

    def pre_load(self):
        try:
            __import__("keystone")
        except ImportError:
            msg = "Missing Python `keystone-engine` package. "
            raise GefMissingDependencyException(msg)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if len(argv) not in (1, 2):
            err("Incorrect syntax")
            self.usage()
            return

        if len(argv) == 2:
            perm = int(argv[1])
        else:
            perm = Permission.READ | Permission.WRITE | Permission.EXECUTE

        loc = long(gdb.parse_and_eval(argv[0]))
        sect = process_lookup_address(loc)
        size = sect.page_end - sect.page_start
        original_pc = current_arch.pc

        info("Generating sys_mprotect({:#x}, {:#x}, '{:s}') stub for arch {:s}".format(sect.page_start, size, Permission(value=perm), get_arch()))
        stub = self.get_stub_by_arch(sect.page_start, size, perm)
        if stub is None:
            err("Failed to generate mprotect opcodes")
            return

        info("Saving original code")
        original_code = read_memory(original_pc, len(stub))

        bp_loc = "*{:#x}".format(original_pc + len(stub))
        info("Setting a restore breakpoint at {:s}".format(bp_loc))
        ChangePermissionBreakpoint(bp_loc, original_code, original_pc)

        info("Overwriting current memory at {:#x} ({:d} bytes)".format(loc, len(stub)))
        write_memory(original_pc, stub, len(stub))

        info("Resuming execution")
        gdb.execute("continue")
        return

    def get_stub_by_arch(self, addr, size, perm):
        code = current_arch.mprotect_asm(addr, size, perm)
        arch, mode = get_keystone_arch()
        raw_insns = keystone_assemble(code, arch, mode, raw=True)
        return raw_insns


class UnicornEmulateCommand(GenericCommand):
    """Use Unicorn-Engine to emulate the behavior of the binary, without affecting the GDB runtime.
    By default the command will emulate only the next instruction, but location and number of instruction can be
    changed via arguments to the command line. By default, it will emulate the next instruction from current PC."""

    _cmdline_ = "unicorn-emulate"
    _syntax_  = "{:s} [-f LOCATION] [-t LOCATION] [-n NB_INSTRUCTION] [-e PATH] [-h]".format(_cmdline_)
    _aliases_ = ["emulate",]

    def __init__(self):
        super(UnicornEmulateCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        self.add_setting("verbose", False, "Set unicorn-engine in verbose mode")
        self.add_setting("show_disassembly", False, "Show every instruction executed")
        return

    def help(self):
        h = self._syntax_
        h += "\n\t-f LOCATION specifies the start address of the emulated run (default $pc).\n"
        h += "\t-t LOCATION specifies the end address of the emulated run.\n"
        h += "\t-e /PATH/TO/SCRIPT.py generates a standalone Python script from the current runtime context.\n"
        h += "\t-n NB_INSTRUCTION indicates the number of instructions to execute (mutually exclusive with `-t` and `-g`).\n"
        h += "\t-g NB_GADGET indicates the number of gadgets to execute (mutually exclusive with `-t` and `-n`).\n"
        h += "\nAdditional options can be setup via `gef config unicorn-emulate`\n"
        info(h)
        return

    def pre_load(self):
        try:
            __import__("unicorn")
            __import__("capstone")
        except ImportError:
            msg = "This command requires the following packages: `unicorn` and `capstone`."
            raise GefMissingDependencyException(msg)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        start_insn = None
        end_insn = -1
        self.nb_insn = -1
        self.until_next_gadget = -1
        to_script = None
        opts = getopt.getopt(argv, "f:t:n:e:g:h")[0]
        for o,a in opts:
            if   o == "-f":   start_insn = int(a, 16)
            elif o == "-t":
                end_insn = int(a, 16)
                self.nb_insn = -1
                self.until_next_gadget = -1

            elif o == "-g":
                self.until_next_gadget = int(a)
                self.nb_insn = -1
                end_insn = -1

            elif o == "-n":
                self.nb_insn = int(a)
                self.until_next_gadget = -1
                end_insn = -1

            elif o == "-e":
                to_script = a

            elif o == "-h":
                self.help()
                return

        if start_insn is None:
            start_insn = current_arch.pc

        if end_insn == -1 and self.nb_insn == -1 and self.until_next_gadget == -1:
            err("No stop condition (-t|-n|-g) defined.")
            return

        self.run_unicorn(start_insn, end_insn, to_script=to_script)
        return

    def get_unicorn_end_addr(self, start_addr, nb):
        dis = list(gef_disassemble(start_addr, nb +1, True))
        return dis[-1][0]

    def run_unicorn(self, start_insn_addr, end_insn_addr, *args, **kwargs):
        start_regs = {}
        end_regs = {}
        verbose = self.get_setting("verbose") or False
        to_script = kwargs.get("to_script", None)
        content = ""
        arch, mode = get_unicorn_arch(to_string=to_script)
        unicorn_registers = get_unicorn_registers(to_string=to_script)
        fname = get_filename()

        if to_script:
            content += """#!/usr/bin/python
#
# Emulation script for '%s' from %#x to %#x
#
import readline, code
import capstone, unicorn

regs = {%s}
uc = None


def disassemble(code, addr):
    cs = capstone.Cs(%s, %s)
    for i in cs.disasm(str(code),addr):
        return i


def hook_code(emu, address, size, user_data):
    print(">> Executing instruction at 0x{:x}".format(address))
    code = emu.mem_read(address, size)
    insn = disassemble(code, address)
    print(">>> 0x{:x}: {:s} {:s}".format(insn.address, insn.mnemonic, insn.op_str))
    return


def interact(emu, regs):
    readline.parse_and_bind("tab: complete")
    vars = globals().copy()
    vars.update(locals())
    code.InteractiveConsole(vars).interact(banner="[+] Spawning Python interactive shell with Unicorn, use `uc` to interact with the emulated session")
    return


def print_regs(emu, regs):
    for r in regs:
        print(">> {:s} = 0x{:x}".format(r, emu.reg_read(regs[r])))
    return


def reset():
""" % (fname, start_insn_addr, end_insn_addr, ",".join(["'%s': %s" % (k.strip(), unicorn_registers[k]) for k in unicorn_registers]), arch, mode)

        unicorn = sys.modules["unicorn"]
        if verbose:
            info("Initializing Unicorn engine")

        if to_script:
            content += "    emu = unicorn.Uc(%s, %s)\n" % (arch, mode)
        else:
            emu = unicorn.Uc(arch, mode)

        if verbose:
            info("Populating registers")

        for r in current_arch.all_registers:
            gregval = get_register_ex(r)
            if to_script:
                content += "    emu.reg_write(%s, %#x)\n" % (unicorn_registers[r], gregval)
            else:
                emu.reg_write(unicorn_registers[r], gregval)
                start_regs[r] = gregval

        vmmap = get_process_maps()
        if vmmap is None or len(vmmap) == 0:
            warn("An error occured when reading memory map.")
            return

        if verbose:
            info("Duplicating memory map")

        # Hack hack hack (- again !!)
        # Because of fs/gs registers used for different purposes (canary and stuff), we map
        # the NULL page as RW- to allow UC to treat instructions dealing with those regs
        # If anybody has a better approach, please send me a PR ;)
        if is_x86_32() or is_x86_64():
            page_sz = resource.getpagesize()
            FS = 0x00
            GS = FS + page_sz
            if to_script:
                content += "    emu.mem_map(%#x, %d, %d)\n" % (FS, page_sz, 3)
                content += "    emu.mem_map(%#x, %d, %d)\n" % (GS, page_sz, 3)
                content += "    emu.reg_write(%s, %#x)\n" % (unicorn_registers["$fs    "], FS)
                content += "    emu.reg_write(%s, %#x)\n" % (unicorn_registers["$gs    "], GS)
            else:
                emu.mem_map(FS, page_sz, 3)
                emu.mem_map(GS, page_sz, 3)
                emu.reg_write(unicorn_registers["$fs    "], FS)
                emu.reg_write(unicorn_registers["$gs    "], GS)


        for sect in vmmap:
            try:
                page_start = sect.page_start
                page_end   = sect.page_end
                size       = sect.size
                perm       = sect.permission

                if to_script:
                    content += "    # Mapping %s: %#x-%#x\n"%(sect.path, page_start, page_end)
                    content += "    emu.mem_map(%#x, %#x, %d)\n" % (page_start, size, perm.value)
                else:
                    emu.mem_map(page_start, size, perm.value)

                if perm & Permission.READ:
                    code = read_memory(page_start, size)
                    if verbose:
                        info("Populating path=%s page=%#x-%#x size=%d perm=%s" % (sect.path,
                                                                                  page_start,
                                                                                  page_end,
                                                                                  size,
                                                                                  perm))

                    if to_script:
                        loc = "/tmp/gef-%s-%#x.raw" % (fname, page_start)
                        with open(loc, "wb") as f:
                            f.write(bytes(code))

                        content += "    emu.mem_write(%#x, open('%s', 'r').read())\n" % (page_start, loc)
                        content += "\n"

                    else:
                        emu.mem_write(page_start, bytes(code))
            except Exception as e:
                warn("Cannot copy page=%#x-%#x : %s" % (page_start, page_end, e))
                continue

        if to_script:
            content += "    emu.hook_add(unicorn.UC_HOOK_CODE, hook_code)\n"
            content += "    return emu\n"
        else:
            emu.hook_add(unicorn.UC_HOOK_BLOCK, self.hook_block)
            emu.hook_add(unicorn.UC_HOOK_CODE, self.hook_code)

        if to_script:
            content += """
def emulate(emu, start_addr, end_addr):
    # Registers initial states
    print_regs(emu, regs)

    try:
        emu.emu_start(start_addr, end_addr)
    except Exception as e:
        emu.emu_stop()
        print("Error: {}".format(e))

    # Registers final states
    print_regs(emu, regs)
    return


if __name__ == "__main__":
    uc = reset()
    emulate(uc, %#x, %#x)
    interact(uc, regs)

# unicorn-engine script generated by gef
""" % (start_insn_addr, end_insn_addr)

            with open(to_script, "w") as f:
                f.write(content)

            info("Unicorn script generated as '%s'" % to_script)
            return

        ok("Starting emulation: %#x %s %#x" % (start_insn_addr,
                                               right_arrow,
                                               end_insn_addr))

        try:
            emu.emu_start(start_insn_addr, end_insn_addr)
        except unicorn.UcError as e:
            emu.emu_stop()
            err("An error occured during emulation: %s" % e)
            return

        ok("Emulation ended, showing %s registers:" % Color.redify("tainted"))

        for r in current_arch.all_registers:
            # ignoring $fs and $gs because of the dirty hack we did to emulate the selectors
            if r in ("$gs    ", "$fs    "): continue

            end_regs[r] = emu.reg_read(unicorn_registers[r])
            tainted = (start_regs[r] != end_regs[r])

            if not tainted:
                continue

            msg = ""
            if r != current_arch.flag_register:
                msg = "%-10s : old=%#016x || new=%#016x" % (r.strip(), start_regs[r], end_regs[r])
            else:
                msg = "%-10s : old=%s \n" % (r.strip(), current_arch.flag_register_to_human(start_regs[r]))
                msg += "%-16s new=%s" % ("", current_arch.flag_register_to_human(end_regs[r]),)

            ok(msg)

        return

    def hook_code(self, emu, addr, size, misc):
        if self.nb_insn == 0:
            ok("Stopping emulation on user's demand (max_instructions reached)")
            emu.emu_stop()
            return

        if self.get_setting("show_disassembly"):
            CapstoneDisassembleCommand.disassemble(addr, 1)

        self.nb_insn -= 1
        return

    def hook_block(self, emu, addr, size, misc):
        if self.until_next_gadget == 0:
            ok("Stopping emulation on user's demand (max_gadgets reached)")
            emu.emu_stop()
            return

        if self.get_setting("show_disassembly"):
            addr_s = format_address(addr)
            info("Entering new block at {:s}".format(addr_s))

        self.until_next_gadget -= 1
        return


class RemoteCommand(GenericCommand):
    """gef wrapper for the `target remote` command. This command will automatically
    download the target binary in the local temporary directory (defaut /tmp) and then
    source it. Additionally, it will fetch all the /proc/PID/maps and loads all its
    information."""

    _cmdline_ = "gef-remote"
    _syntax_  = "{:s} [OPTIONS] TARGET".format(_cmdline_)

    def __init__(self):
        super(RemoteCommand, self).__init__(prefix=False)
        self.handler_connected = False
        return

    def do_invoke(self, argv):
        target = None
        rpid = -1
        update_solib = False
        self.download_all_libs = False
        download_lib = None
        is_extended_remote = False
        opts, args = getopt.getopt(argv, "p:UD:AEh")
        for o,a in opts:
            if   o == "-U":   update_solib = True
            elif o == "-D":   download_lib = a
            elif o == "-A":   self.download_all_libs = True
            elif o == "-E":   is_extended_remote = True
            elif o == "-p":   rpid = int(a)
            elif o == "-h":
                self.help()
                return

        if args is None or len(args)!=1 or rpid < 0:
            err("A target (HOST:PORT) *and* a PID (-p PID) must always be provided.")
            return

        # lazily install handler on first use
        if not self.handler_connected:
            gdb.events.new_objfile.connect(self.new_objfile_handler)
            self.handler_connected = True

        target = args[0]

        if self.connect_target(target, is_extended_remote) == False:
            return

        # if extended-remote, need to attach
        if is_extended_remote:
            ok("Attaching to {:d}".format(rpid))
            disable_context()
            gdb.execute("attach {:d}".format(rpid))
            enable_context()
        else:
            ok("Targeting PID={:d}".format(rpid))

        self.add_setting("target", target, "Remote target to connect to")
        self.setup_remote_environment(rpid, update_solib)

        if not is_remote_debug():
            warn("No remote session active.")
            return

        if self.download_all_libs == True:
            vmmap = get_process_maps()
            success = 0
            for sect in vmmap:
                if sect.path.startswith("/"):
                    _file = download_file(sect.path)
                    if _file is None:
                        err("Failed to download {:s}".format(sect.path))
                    else:
                        success += 1

            ok("Downloaded {:d} files".format(success))

        elif download_lib is not None:
            _file = download_file(download_lib)
            if _file is None:
                err("Failed to download remote file")
                return

            ok("Download success: {:s} {:s} {:s}".format(download_lib, right_arrow, _file))

        if update_solib:
            self.refresh_shared_library_path()

        set_arch()

        return

    def new_objfile_handler(self, event):
        """Hook that handles new_objfile events, will update remote environment accordingly"""
        if not is_remote_debug():
            return

        if self.download_all_libs and event.new_objfile.filename.startswith("target:"):
            lib = event.new_objfile.filename[len("target:"):]
            llib = download_file(lib, use_cache=True)
            if llib:
                ok("Download success: {:s} {:s} {:s}".format(lib, right_arrow, llib))
        return

    def setup_remote_environment(self, pid, update_solib=False):
        """Clone the remote environment locally in the temporary directory.
        The command will duplicate the entries in the /proc/<pid> locally and then
        source those information into the current gdb context to allow gef to use
        all the extra commands as it was local debugging."""
        gdb.execute("reset-cache")

        ok("Downloading remote information")
        infos = {}
        for i in ["exe", "maps", "environ", "cmdline"]:
            infos[i] = self.load_target_proc(pid, i)
            if infos[i] is None:
                err("Failed to load memory map of '{:s}'".format(i))
                return

        if not os.access(infos["exe"], os.R_OK):
            err("Source binary is not readable")
            return

        directory  = GEF_TEMP_DIR
        gdb.execute("file {:s}".format(infos["exe"]))
        self.add_setting("root", directory, "Path to store the remote data")
        ok("Remote information loaded, remember to clean '{:s}' when your session is over".format(directory))
        return

    def connect_target(self, target, is_extended_remote):
        """Connect to remote target and get symbols. To prevent `gef` from requesting information
        not fetched just yet, we disable the context disable when connection was successful."""
        disable_context()
        try:
            cmd = "target {} {}".format("extended-remote" if is_extended_remote else "remote", target)
            gdb.execute(cmd)
            ok("Connected to '{}'".format(target))
            ret = True
        except Exception as e:
            err("Failed to connect to {:s}: {:s}".format(target, str(e)))
            ret = False
        enable_context()
        return ret


    def load_target_proc(self, pid, info):
        """Download one item from /proc/pid"""
        remote_name = "/proc/{:d}/{:s}".format(pid, info)
        return download_file(remote_name)


    def refresh_shared_library_path(self):
        dirs = [r for r, d, f in os.walk(self.get_setting("root"))]
        path = ":".join(dirs)
        gdb.execute("set solib-search-path {:s}".format(path,))
        return


    def help(self):
        h = self._syntax_
        h += "\n\t   TARGET (mandatory) specifies the host:port, serial port or tty to connect to.\n"
        h += "\t-U will update gdb `solib-search-path` attribute to include the files downloaded from server (default: False).\n"
        h += "\t-A will download *ALL* the remote shared libraries and store them in the new environment. This command can take a few minutes to complete (default: False).\n"
        h += "\t-D LIB will download the remote library called LIB.\n"
        h += "\t-E Use 'extended-remote' to connect to the target.\n"
        h += "\t-p PID (mandatory if -E is used) specifies PID of the debugged process on gdbserver's end.\n"
        info(h)
        return


class NopCommand(GenericCommand):
    """Patch the instruction(s) pointed by parameters with NOP."""

    _cmdline_ = "nop"
    _syntax_  = "{:s} [-b NUM_BYTES] [-h] [LOCATION]".format(_cmdline_)


    def __init__(self):
        super(NopCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        return


    def get_insn_size(self, addr):
        insns = [x[0] for x in gef_disassemble(addr, 1, True)]
        return insns[1] - insns[0]


    def do_invoke(self, argv):
        opts, args = getopt.getopt(argv, "b:h")
        num_bytes = 0
        for o, a in opts:
            if o == "-b":
                num_bytes = long(a, 0)
            elif o == "-h":
                self.help()
                return

        if args:
            loc = parse_address(args[0])
        else:
            loc = current_arch.pc

        self.nop_bytes(loc, num_bytes)
        return


    def help(self):
        m = self._syntax_
        m += "\n  LOCATION\taddress/symbol to patch\n"
        m += "  -b NUM_BYTES\tInstead of writing one instruction, patch the specified number of bytes\n"
        m += "  -h \t\tprint this help\n"
        info(m)
        return

    @if_gdb_running
    def nop_bytes(self, loc, num_bytes):
        if num_bytes == 0:
            size = self.get_insn_size(loc)
        else:
            size = num_bytes
        nops = current_arch.nop_insn

        if len(nops) > size:
            m = "Cannot patch instruction at {:#x} (nop_size is:{:d},insn_size is:{:d})".format(loc, len(nops), size)
            err(m)
            return

        while len(nops) < size:
            nops += current_arch.nop_insn

        if len(nops) != size:
            err("Cannot patch instruction at {:#x} (nop instruction does not evenly fit in requested size)"
                .format(loc))
            return

        ok("Patching {:d} bytes from {:s}".format(size, format_address(loc)))
        write_memory(loc, nops, size)

        return


class StubCommand(GenericCommand):
    """Stub out the specified function."""

    _cmdline_ = "stub"
    _syntax_  = "{:s} [-r RETVAL] [-h] [LOCATION]".format(_cmdline_)

    def __init__(self):
        super(StubCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        return

    def do_invoke(self, argv):
        opts, args = getopt.getopt(argv, "r:h")
        retval = 0
        for o, a in opts:
            if o == "-r":
                retval = long(a, 0)
            elif o == "-h":
                self.help()
                return

        if not args:
            loc = "*{:#x}".format(current_arch.pc)
        else:
            loc = args[0]

        self.stub_out(loc, retval)
        return

    def help(self):
        m = [self._syntax_]
        m.append("  LOCATION\taddress/symbol to stub out")
        m.append("  -b RETVAL\tSet the return value")
        m.append("  -h \t\tprint this help")
        info("\n".join(m))
        return

    @if_gdb_running
    def stub_out(self, loc, retval):
        StubBreakpoint(loc, retval)
        return


class CapstoneDisassembleCommand(GenericCommand):
    """Use capstone disassembly framework to disassemble code."""

    _cmdline_ = "capstone-disassemble"
    _syntax_  = "{:s} [-n LENGTH] [-t opt] [LOCATION]".format(_cmdline_)
    _aliases_ = ["cs-dis",]

    def pre_load(self):
        try:
            __import__("capstone")
        except ImportError:
            msg = "Missing Python `capstone` package. "
            raise GefMissingDependencyException(msg)
        return


    def __init__(self):
        super(CapstoneDisassembleCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        location, length = current_arch.pc, 0x10
        opts, args = getopt.getopt(argv, "n:x:")
        for o, a in opts:
            if o == "-n":
                length = long(a)
            elif o == "-x":
                k, v = a.split(":", 1)
                self.add_setting(k, v)

        if args:
            location = parse_address(args[0])

        kwargs = {}
        if self.has_setting("arm_thumb"):
            kwargs["arm_thumb"] = True

        if self.has_setting("mips_r6"):
            kwargs["mips_r6"] = True

        CapstoneDisassembleCommand.disassemble(location, length, **kwargs)
        return


    @staticmethod
    def disassemble(location, max_inst, *args, **kwargs):
        capstone    = sys.modules["capstone"]
        arch, mode  = get_capstone_arch()
        cs          = capstone.Cs(arch, mode)
        cs.detail   = True

        page_start  = align_address_to_page(location)
        offset      = location - page_start
        inst_num    = 0
        pc          = current_arch.pc

        code        = kwargs.get("code", None)
        if code is None:
            code  = read_memory(location, DEFAULT_PAGE_SIZE - offset - 1)

        code = bytes(code)

        for insn in cs.disasm(code, location):
            m = Color.colorify(format_address(insn.address), attrs="bold blue") + "\t"

            if insn.address == pc:
                m += CapstoneDisassembleCommand.__cs_analyze_insn(insn, arch, True)
            else:
                m += Color.greenify(insn.mnemonic) + "\t"
                m += Color.yellowify(insn.op_str)

            print(m)
            inst_num += 1
            if inst_num == max_inst:
                break

        return


    @staticmethod
    def __cs_analyze_insn(insn, arch, is_pc=True):
        cs = sys.modules["capstone"]

        m = ""
        m += Color.greenify(insn.mnemonic)
        m += "\t"
        m += Color.yellowify(insn.op_str)

        if is_pc:
            m += Color.redify("\t {} $pc ".format(left_arrow))

        m += "\n" + "\t" * 5

        # implicit read
        if insn.regs_read:
            m += "Read:[{:s}] ".format(",".join([insn.reg_name(x) for x in insn.regs_read]))
            m += "\n" + "\t" * 5

        # implicit write
        if insn.regs_write:
            m += "Write:[{:s}] ".format(",".join([insn.reg_name(x) for x in insn.regs_write]))
            m += "\n" + "\t" * 5

        if   is_x86_32():  reg, imm, mem = cs.x86.X86_OP_REG, cs.x86.X86_OP_IMM, cs.x86.X86_OP_MEM
        elif is_x86_64():  reg, imm, mem = cs.x86.X86_OP_REG, cs.x86.X86_OP_IMM, cs.x86.X86_OP_MEM
        elif is_powerpc(): reg, imm, mem = cs.ppc.PPC_OP_REG, cs.ppc.PPC_OP_IMM, cs.ppc.PPC_OP_MEM
        elif is_mips():    reg, imm, mem = cs.mips.MIPS_OP_REG, cs.mips.MIPS_OP_IMM, cs.mips.MIPS_OP_MEM
        elif is_sparc():   reg, imm, mem = cs.sparc.SPARC_OP_REG, cs.sparc.SPARC_OP_IMM, cs.sparc.SPARC_OP_MEM
        elif is_sparc64(): reg, imm, mem = cs.sparc.SPARC_OP_REG, cs.sparc.SPARC_OP_IMM, cs.sparc.SPARC_OP_MEM
        elif is_arm():     reg, imm, mem = cs.arm.ARM_OP_REG, cs.arm.ARM_OP_IMM, cs.arm.ARM_OP_MEM
        elif is_aarch64(): reg, imm, mem = cs.arm.ARM_OP_REG, cs.arm.ARM_OP_IMM, cs.arm.ARM_OP_MEM

        # operand information
        for op in insn.operands:
            if op.type == reg:
                m += "REG={:s} ".format(insn.reg_name(op.value.reg),)
            if op.type == imm:
                m += "IMM={:#x} ".format(op.value.imm,)
            if op.type == mem:
                if op.value.mem.disp > 0:
                    m += "MEM={:s}+{:#x} ".format(insn.reg_name(op.value.mem.base), op.value.mem.disp,)
                elif op.value.mem.disp < 0:
                    m += "MEM={:s}{:#x} ".format(insn.reg_name(op.value.mem.base), op.value.mem.disp,)

            m += "\n" + "\t" * 5

        return m


class GlibcHeapCommand(GenericCommand):
    """Base command to get information about the Glibc heap structure."""

    _cmdline_ = "heap"
    _syntax_  = "{:s} (chunk|bins|arenas)".format(_cmdline_)

    def do_invoke(self, argv):
        self.usage()
        return

    @staticmethod
    def get_main_arena():
        try:
            arena = GlibcArena("main_arena")
        except Exception:
            warn("Failed to get `main_arena` symbol. heap commands may not work properly")
            arena = None
        return arena


class GlibcHeapArenaCommand(GenericCommand):
    """Display information on a heap chunk."""

    _cmdline_ = "heap arenas"
    _syntax_  = _cmdline_

    def __init__(self):
        super(GlibcHeapArenaCommand, self).__init__(prefix=False)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        ok("Listing active arena(s):")
        try:
            arena = GlibcArena("main_arena")
        except Exception:
            info("Could not find Glibc main arena")
            return

        while True:
            print("{}".format(arena))
            arena = arena.get_next()
            if arena is None:
                break
        return


class GlibcHeapChunkCommand(GenericCommand):
    """Display information on a heap chunk.
    See https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1123"""

    _cmdline_ = "heap chunk"
    _syntax_  = "{:s} MALLOCED_LOCATION".format(_cmdline_)

    def __init__(self):
        super(GlibcHeapChunkCommand, self).__init__(prefix=False, complete=gdb.COMPLETE_LOCATION)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if len(argv) < 1:
            err("Missing chunk address")
            self.usage()
            return

        GlibcHeapCommand.get_main_arena()

        addr = long(gdb.parse_and_eval(argv[0]))
        chunk = GlibcChunk(addr)
        chunk.pprint()
        return

class GlibcHeapBinsCommand(GenericCommand):
    """Display information on the bins on an arena (default: main_arena).
    See https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1123"""

    _bins_type_ = ["fast", "unsorted", "small", "large"]
    _cmdline_ = "heap bins"
    _syntax_ = "{:s} [{:s}]".format(_cmdline_, "|".join(_bins_type_))

    @if_gdb_running
    def do_invoke(self, argv):
        if len(argv) == 0:
            for bin_t in GlibcHeapBinsCommand._bins_type_:
                gdb.execute("heap bins {:s}".format(bin_t))
            return

        bin_t = argv[0]
        if bin_t not in GlibcHeapBinsCommand._bins_type_:
            self.usage()
            return

        gdb.execute("heap bins {}".format(bin_t))
        return

    @staticmethod
    def pprint_bin(arena_addr, index):
        arena = GlibcArena(arena_addr)
        fw, bk = arena.bin(index)

        if bk==0x00 and fw==0x00:
            warn("Invalid backward and forward bin pointers(fw==bk==NULL)")
            return -1

        ok("Found base for bin({:d}): fw={:#x}, bk={:#x}".format(index, fw, bk))
        if bk == fw and ((int(arena)&~0xFFFF) == (bk&~0xFFFF)):
            ok("Empty")
            return 0

        m = ""
        head = GlibcChunk(bk + 2 * arena.get_arch()).get_fwd_ptr()
        while fw != head:
            chunk = GlibcChunk(fw + 2 * arena.get_arch())
            m += "{:s}  {:s}  ".format(right_arrow, str(chunk))
            fw = chunk.get_fwd_ptr()

        print(m)
        return 0


class GlibcHeapFastbinsYCommand(GenericCommand):
    """Display information on the fastbinsY on an arena (default: main_arena).
    See https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1123"""

    _cmdline_ = "heap bins fast"
    _syntax_  = "{:s} [ARENA_ADDRESS]".format(_cmdline_)

    def __init__(self):
        super(GlibcHeapFastbinsYCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        main_arena = GlibcHeapCommand.get_main_arena()
        arena = GlibcArena("*{:s}".format(argv[0])) if len(argv) == 1 else main_arena

        if arena is None:
            err("Invalid Glibc arena")
            return

        print(titlify("Fastbins for arena {:#x}".format(int(arena))))
        for i in range(10):
            print("Fastbin[{:d}] ".format(i), end="")
            # https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1680
            chunk = arena.fastbin(i)

            while True:
                if chunk is None:
                    print("0x00", end="")
                    break

                print("{:s}  {:s}  ".format(right_arrow, str(chunk)), end="")
                try:
                    next_chunk = chunk.get_fwd_ptr()
                    if next_chunk == 0:
                        break

                    chunk = GlibcChunk(next_chunk, from_base=True)
                except gdb.MemoryError:
                    break
            print()

        return


class GlibcHeapUnsortedBinsCommand(GenericCommand):
    """Display information on the Unsorted Bins of an arena (default: main_arena).
    See: https://github.com/sploitfun/lsploits/blob/master/glibc/malloc/malloc.c#L1689"""

    _cmdline_ = "heap bins unsorted"
    _syntax_  = "{:s} [ARENA_ADDRESS]".format(_cmdline_)

    def __init__(self):
        super(GlibcHeapUnsortedBinsCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if GlibcHeapCommand.get_main_arena() is None:
            err("Incorrect Glibc arenas")
            return

        arena_addr = "*{:s}".format(argv[0]) if len(argv) == 1 else "main_arena"
        print(titlify("Unsorted Bin for arena '{:s}'".format(arena_addr)))
        GlibcHeapBinsCommand.pprint_bin(arena_addr, 0)
        return


class GlibcHeapSmallBinsCommand(GenericCommand):
    """Convenience command for viewing small bins."""

    _cmdline_ = "heap bins small"
    _syntax_  = "{:s} [ARENA_ADDRESS]".format(_cmdline_)

    def __init__(self):
        super(GlibcHeapSmallBinsCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if GlibcHeapCommand.get_main_arena() is None:
            err("Incorrect Glibc arenas")
            return

        arena_addr = "*{:s}".format(argv[0]) if len(argv) == 1 else "main_arena"
        print(titlify("Small Bins for arena '{:s}'".format(arena_addr)))
        for i in range(1, 64):
            if GlibcHeapBinsCommand.pprint_bin(arena_addr, i) < 0:
                break

        return


class GlibcHeapLargeBinsCommand(GenericCommand):
    """Convenience command for viewing large bins."""

    _cmdline_ = "heap bins large"
    _syntax_  = "{:s} [ARENA_ADDRESS]".format(_cmdline_)

    def __init__(self):
        super(GlibcHeapLargeBinsCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if GlibcHeapCommand.get_main_arena() is None:
            err("Incorrect Glibc arenas")
            return

        arena_addr = "*{:s}".format(argv[0]) if len(argv) == 1 else "main_arena"
        print(titlify("Large Bins for arena '{:s}'".format(arena_addr)))
        for i in range(64, 127):
            if GlibcHeapBinsCommand.pprint_bin(arena_addr, i)<0:
                break
        return


class SolveKernelSymbolCommand(GenericCommand):
    """Solve kernel symbols from kallsyms table."""

    _cmdline_ = "ksymaddr"
    _syntax_  = "{:s} SymbolToSearch".format(_cmdline_)

    def do_invoke(self, argv):
        if len(argv) != 1:
            self.usage()
            return

        found = False
        sym = argv[0]
        with open("/proc/kallsyms", "r") as f:
            for line in f:
                try:
                    symaddr, symtype, symname = line.strip().split(" ", 3)
                    symaddr = long(symaddr, 16)
                    if symname == sym:
                        ok("Found matching symbol for '{:s}' at {:#x} (type={:s})".format(sym, symaddr, symtype))
                        found = True
                    if sym in symname:
                        warn("Found partial match for '{:s}' at {:#x} (type={:s}): {:s}".format(sym, symaddr, symtype, symname))
                        found = True
                except ValueError:
                    pass

        if not found:
            err("No match for '{:s}'".format(sym))
        return


class DetailRegistersCommand(GenericCommand):
    """Display full details on one, many or all registers value from current architecture."""

    _cmdline_ = "registers"
    _syntax_  = "{:s} [Register1] [Register2] ... [RegisterN]".format(_cmdline_)

    @if_gdb_running
    def do_invoke(self, argv):
        regs = []
        regname_color = __config__.get("theme.registers_register_name")[0]

        if argv:
            regs = [reg for reg in current_arch.all_registers if reg.strip() in argv]
        else:
            regs = current_arch.all_registers

        for regname in regs:
            reg = gdb.parse_and_eval(regname)
            if reg.type.code == gdb.TYPE_CODE_VOID:
                continue

            line = ""
            line+= Color.colorify(regname, attrs=regname_color)
            line+= ": "

            if str(reg) == "<unavailable>":
                line += Color.colorify("no value", attrs="yellow underline")
                print(line)
                continue

            if reg.type.code == gdb.TYPE_CODE_FLAGS:
                line += current_arch.flag_register_to_human()
                print(line)
                continue

            addr = align_address(long(reg))
            line += Color.boldify(format_address(addr))
            addrs = DereferenceCommand.dereference_from(addr)

            if len(addrs) > 1:
                sep = " {:s} ".format(right_arrow)
                line += sep
                line += sep.join(addrs[1:])

            print(line)
        return


class ShellcodeCommand(GenericCommand):
    """ShellcodeCommand uses @JonathanSalwan simple-yet-awesome shellcode API to
    download shellcodes."""

    _cmdline_ = "shellcode"
    _syntax_  = "{:s} <search|get>".format(_cmdline_)


    def do_invoke(self, argv):
        err("Missing sub-command <search|get>")
        self.usage()
        return


class ShellcodeSearchCommand(GenericCommand):
    """Search pattern in shellcodes database."""

    _cmdline_ = "shellcode search"
    _syntax_  = "{:s} <pattern1> <pattern2>".format(_cmdline_)
    _aliases_ = ["sc-search",]

    api_base = "http://shell-storm.org"
    search_url = "{}/api/?s=".format(api_base)


    def do_invoke(self, argv):
        if len(argv) == 0:
            err("Missing pattern to search")
            self.usage()
        else:
            self.search_shellcode(argv)
        return


    def search_shellcode(self, search_options):
        # API : http://shell-storm.org/shellcode/
        args = "*".join(search_options)

        res = http_get(self.search_url + args)
        if res is None:
            err("Could not query search page")
            return

        ret = gef_pystring(res)

        # format: [author, OS/arch, cmd, id, link]
        lines = ret.split("\n")
        refs = [line.split("::::") for line in lines]

        if refs:
            info("Showing matching shellcodes")
            info("\t".join(["Id", "Platform", "Description"]))
            for ref in refs:
                try:
                    _, arch, cmd, sid, _ = ref
                    print("\t".join([sid, arch, cmd]))
                except ValueError:
                    continue

            info("Use `shellcode get <id>` to fetch shellcode")
        return


class ShellcodeGetCommand(GenericCommand):
    """Download shellcode from shellcodes database"""

    _cmdline_ = "shellcode get"
    _syntax_  = "{:s} <shellcode_id>".format(_cmdline_)
    _aliases_ = ["sc-get",]

    api_base = "http://shell-storm.org"
    get_url = "{}/shellcode/files/shellcode-{{:d}}.php".format(api_base)

    def do_invoke(self, argv):
        if len(argv) != 1:
            err("Missing ID to download")
            self.usage()
            return

        if not argv[0].isdigit():
            err("ID is not a number")
            self.usage()
            return

        self.get_shellcode(long(argv[0]))
        return

    def get_shellcode(self, sid):
        res = http_get(self.get_url.format(sid))
        if res is None:
            err("Failed to fetch shellcode #{:d}".format(sid))
            return

        print(res)
        ret  = gef_pystring(res)

        info("Downloading shellcode id={:d}".format(sid))
        fd, fname = tempfile.mkstemp(suffix=".txt", prefix="sc-", text=True, dir="/tmp")
        data = ret.split("\n")[7:-11]
        buf = "\n".join(data)
        buf = HTMLParser().unescape(buf)
        os.write(fd, gef_pybytes(buf))
        os.close(fd)
        info("Shellcode written to '{:s}'".format(fname))
        return


class RopperCommand(GenericCommand):
    """Ropper (http://scoding.de/ropper) plugin"""

    _cmdline_ = "ropper"
    _syntax_  = "{:s} [OPTIONS]".format(_cmdline_)


    def __init__(self):
        super(RopperCommand, self).__init__(complete=gdb.COMPLETE_NONE)
        return

    def pre_load(self):
        try:
            __import__("ropper")
        except ImportError:
            msg = "Missing Python `ropper` package. "
            raise GefMissingDependencyException(msg)
        return


    def do_invoke(self, argv):
        ropper = sys.modules["ropper"]
        argv.append("--file")
        argv.append(get_filepath())
        try:
            ropper.start(argv)
        except SystemExit:
            return


class ROPgadgetCommand(GenericCommand):
    """ROPGadget (http://shell-storm.org/project/ROPgadget) plugin"""

    _cmdline_ = "ropgadget"
    _syntax_  = "{:s} [OPTIONS]".format(_cmdline_)


    def __init__(self):
        super(ROPgadgetCommand, self).__init__(complete=gdb.COMPLETE_NONE)
        return

    def pre_load(self):
        try:
            __import__("ropgadget")
        except ImportError:
            msg = "Missing Python `ropgadget` package. "
            raise GefMissingDependencyException(msg)
        return


    def do_invoke(self, argv):
        class FakeArgs(object):
            all        = None
            binary     = None
            string     = None
            opcode     = None
            memstr     = None
            console    = None
            norop      = None
            nojop      = None
            depth      = 10
            nosys      = None
            range      = "0x00-0x00"
            badbytes   = None
            only       = None
            filter     = None
            ropchain   = None
            offset     = 0x00
            outfile    = None
            thumb      = None
            rawArch    = None
            rawMode    = None
            multibr    = None


        ropgadget = sys.modules["ropgadget"]
        args = FakeArgs()
        if self.parse_args(args, argv):
            ropgadget.core.Core(args).analyze()
        return


    def parse_args(self, args, argv):
        #
        # options format is 'option_name1=option_value1'
        #
        def __usage__():
            arr = [x for x in dir(args) if not x.startswith("__")]
            info("Valid options for {:s} are:\n{:s}".format(self._cmdline_, ", ".join(arr)))
            return

        for opt in argv:
            if opt in ("?", "h", "help"):
                __usage__()
                return False

            try:
                name, value = opt.split("=")
            except ValueError:
                err("Invalid syntax for argument '{0:s}', should be '{0:s}=<value>'".format(opt))
                __usage__()
                return False

            if hasattr(args, name):
                if name == "console":
                    continue
                elif name == "depth":
                    value = long(value)
                    depth = value
                    info("Using depth {:d}".format(depth))
                elif name == "range":
                    off_min = long(value.split("-")[0], 16)
                    off_max = long(value.split("-")[1], 16)
                    if off_max < off_min:
                        raise ValueError("{:#x} must be higher that {:#x}".format(off_max, off_min))
                    info("Using range [{:#x}:{:#x}] ({:ld} bytes)".format(off_min, off_max, (off_max - off_min)))

                setattr(args, name, value)

            else:
                err("'{:s}' is not a valid ropgadget option".format(name))
                __usage__()
                return False

        if getattr(args, "binary") is None:
            setattr(args, "binary", get_filepath())

        info("Using binary: {:s}".format(args.binary))
        return True


class AssembleCommand(GenericCommand):
    """Inline code assemble. Architecture can be set in GEF runtime config (default is
    x86). """

    _cmdline_ = "assemble"
    _syntax_  = "{:s} [-a ARCH] [-m MODE] [-e] [-s] [-l LOCATION] instruction;[instruction;...instruction;])".format(_cmdline_)
    _aliases_ = ["asm",]

    def __init__(self, *args, **kwargs):
        super(AssembleCommand, self).__init__(prefix=False, complete=gdb.COMPLETE_LOCATION)
        return

    def pre_load(self):
        try:
            __import__("keystone")
        except ImportError:
            msg = "Missing Python `keystone-engine` package. "
            raise GefMissingDependencyException(msg)
        return

    def do_invoke(self, argv):
        arch_s, mode_s, big_endian, as_shellcode, write_to_location = None, None, False, False, None
        opts, args = getopt.getopt(argv, "a:m:l:esh")
        for o,a in opts:
            if o == "-a": arch_s = a.upper()
            if o == "-m": mode_s = a.upper()
            if o == "-e": big_endian = True
            if o == "-s": as_shellcode = True
            if o == "-l": write_to_location = long(gdb.parse_and_eval(a))
            if o == "-h":
                self.usage()
                return

        if not args:
            return

        if (arch_s, mode_s) == (None, None):
            if is_alive():
                arch_s, mode_s = get_arch(), ""
                endian_s = "big" if is_big_endian() else "little"
                arch, mode = get_keystone_arch()
            else:
                # if not alive, defaults to x86-32
                arch_s = "X86"
                mode_s = "32"
                endian_s = "little"
                arch, mode = get_keystone_arch(arch=arch_s, mode=mode_s, endian=False)
        else:
            arch, mode = get_keystone_arch(arch=arch_s, mode=mode_s, endian=big_endian)
            endian_s = "big" if big_endian else "little"

        insns = " ".join(args)
        insns = [x.strip() for x in insns.split(";") if x is not None]

        info("Assembling {} instruction{} for {} ({} endian)".format(len(insns),
                                                                     "s" if len(insns)>1 else "",
                                                                     ":".join([arch_s, mode_s]),
                                                                     endian_s))

        if as_shellcode:
            print("""sc="" """)

        raw = b""
        for insn in insns:
            res = keystone_assemble(insn, arch, mode, raw=True)
            if res is None:
                print("(Invalid)")
                continue

            if write_to_location:
                raw += res
                continue

            s = binascii.hexlify(res)
            res = b"\\x" + b"\\x".join([s[i:i + 2] for i in range(0, len(s), 2)])
            res = res.decode("utf-8")

            if as_shellcode:
                res = """sc+="{0:s}" """.format(res)

            print("{0:60s} # {1}".format(res, insn))

        if write_to_location:
            l = len(raw)
            info("Overwriting {:d} bytes at {:s}".format(l, format_address(write_to_location)))
            write_memory(write_to_location, raw, l)
        return


class ProcessListingCommand(GenericCommand):
    """List and filter process."""

    _cmdline_ = "process-search"
    _syntax_  = "{:s} [PATTERN]".format(_cmdline_)
    _aliases_ = ["ps",]

    def __init__(self):
        super(ProcessListingCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        self.add_setting("ps_command", "/bin/ps auxww", "`ps` command to get process information")
        return

    def do_invoke(self, argv):
        do_attach = False
        smart_scan = False

        opts, args = getopt.getopt(argv, "as")
        for o, _ in opts:
            if o == "-a": do_attach  = True
            if o == "-s": smart_scan = True

        pattern = re.compile("^.*$") if not args else re.compile(args[0])

        for process in self.get_processes():
            pid = int(process["pid"])
            command = process["command"]

            if not re.search(pattern, command):
                continue

            if smart_scan:
                if command.startswith("[") and command.endswith("]"): continue
                if command.startswith("socat "): continue
                if command.startswith("grep "): continue
                if command.startswith("gdb "): continue

            if args and do_attach:
                ok("Attaching to process='{:s}' pid={:d}".format(process["command"], pid))
                gdb.execute("attach {:d}".format(pid))
                return None

            line = [process[i] for i in ("pid", "user", "cpu", "mem", "tty", "command")]
            print("\t\t".join(line))

        return None


    def get_processes(self):
        output = gef_execute_external(self.get_setting("ps_command").split(), True)
        names = [x.lower().replace("%", "") for x in output[0].split()]

        for line in output[1:]:
            fields = line.split()
            t = {}

            for i, name in enumerate(names):
                if i == len(names) - 1:
                    t[name] = " ".join(fields[i:])
                else:
                    t[name] = fields[i]

            yield t

        return


class ElfInfoCommand(GenericCommand):
    """Display ELF header informations."""

    _cmdline_ = "elf-info"
    _syntax_  = _cmdline_

    def __init__(self, *args, **kwargs):
        super(ElfInfoCommand, self).__init__(prefix=False, complete=gdb.COMPLETE_LOCATION)
        return


    def do_invoke(self, argv):
        # http://www.sco.com/developers/gabi/latest/ch4.eheader.html
        classes = {0x01: "32-bit",
                   0x02: "64-bit",}
        endianness = {0x01: "Little-Endian",
                      0x02: "Big-Endian",}
        osabi = {
            0x00: "System V",
            0x01: "HP-UX",
            0x02: "NetBSD",
            0x03: "Linux",
            0x06: "Solaris",
            0x07: "AIX",
            0x08: "IRIX",
            0x09: "FreeBSD",
            0x0C: "OpenBSD",
        }

        types = {
            0x01: "Relocatable",
            0x02: "Executable",
            0x03: "Shared",
            0x04: "Core"
        }

        machines = {
            0x02: "SPARC",
            0x03: "x86",
            0x08: "MIPS",
            0x12: "SPARC64",
            0x14: "PowerPC",
            0x15: "PowerPC64",
            0x28: "ARM",
            0x32: "IA-64",
            0x3E: "x86-64",
            0xB7: "AArch64",
        }

        filename = argv[0] if argv else get_filepath()
        if filename is None:
            return

        elf = get_elf_headers(filename)
        if elf is None:
            return

        data = [
            ("Magic", "{0!s}".format(hexdump(struct.pack(">I",elf.e_magic), show_raw=True))),
            ("Class", "{0:#x} - {1}".format(elf.e_class, classes[elf.e_class])),
            ("Endianness", "{0:#x} - {1}".format(elf.e_endianness, endianness[elf.e_endianness])),
            ("Version", "{:#x}".format(elf.e_eiversion)),
            ("OS ABI", "{0:#x} - {1}".format(elf.e_osabi, osabi[elf.e_osabi])),
            ("ABI Version", "{:#x}".format(elf.e_abiversion)),
            ("Type", "{0:#x} - {1}".format(elf.e_type, types[elf.e_type])),
            ("Machine", "{0:#x} - {1}".format(elf.e_machine, machines[elf.e_machine])),
            ("Program Header Table" , "{}".format(format_address(elf.e_phoff))),
            ("Section Header Table" , "{}".format(format_address(elf.e_shoff))),
            ("Header Table" , "{}".format(format_address(elf.e_phoff))),
            ("ELF Version", "{:#x}".format(elf.e_version)),
            ("Header size" , "{0} ({0:#x})".format(elf.e_ehsize)),
            ("Entry point", "{}".format(format_address(elf.e_entry))),
        ]

        for title, content in data:
            print("{:<30}: {}".format(Color.boldify(title), content))
        return


class EntryPointBreakCommand(GenericCommand):
    """Tries to find best entry point and sets a temporary breakpoint on it."""

    _cmdline_ = "entry-break"
    _syntax_  = _cmdline_
    _aliases_ = ["start",]

    def __init__(self):
        super(EntryPointBreakCommand, self).__init__(prefix=False)
        return

    def do_invoke(self, argv):
        fpath = get_filepath()
        if fpath is None:
            warn("No executable to debug, use `file` to load a binary")
            return

        if not os.access(fpath, os.X_OK):
            warn("The file '{}' is not executable.".format(fpath))
            return

        if is_alive():
            warn("gdb is already running")
            return

        syms = ["main", "__libc_start_main", "__uClibc_main"]
        bp = -1
        for sym in syms:
            try:
                value = gdb.parse_and_eval(sym)
                info("Breaking at '{:s}'".format(str(value)))
                bp = gdb.execute("tbreak {:s}".format(sym), from_tty=True, to_string=True)
                bp = int(bp.split()[2])
                gdb.execute("run {}".format(" ".join(argv)))
                return

            except gdb.error as gdb_error:
                if 'The "remote" target does not support "run".' in str(gdb_error):
                    # this case can happen when doing remote debugging
                    gdb.execute("continue")
                    return
                continue

        # if here, clear the breakpoint if any set
        if bp >= 0:
            gdb.execute("delete breakpoints {:d}".format(bp))

        # break at entry point
        elf = get_elf_headers()
        if elf is None:
            return

        if self.is_pie(fpath):
            self.set_init_tbreak_pie(elf.e_entry)
            gdb.execute("continue")
            return

        self.set_init_tbreak(elf.e_entry)
        gdb.execute("run")
        return

    def set_init_tbreak(self, addr):
        info("Breaking at entry-point: {:#x}".format(addr))
        bp_num = gdb.execute("tbreak *{:#x}".format(addr), to_string=True)
        bp_num = int(bp_num.split()[2])
        return bp_num

    def set_init_tbreak_pie(self, addr):
        warn("PIC binary detected, retrieving text base address")
        enable_redirect_output()
        gdb.execute("set stop-on-solib-events 1")
        disable_context()
        gdb.execute("run")
        enable_context()
        gdb.execute("set stop-on-solib-events 0")
        vmmap = get_process_maps()
        base_address = [x.page_start for x in vmmap if x.path == get_filepath()][0]
        disable_redirect_output()
        return self.set_init_tbreak(base_address + addr)

    def is_pie(self, fpath):
        return checksec(fpath)["PIE"]


class ContextCommand(GenericCommand):
    """Display execution context."""

    _cmdline_ = "context"
    _syntax_  = _cmdline_
    _aliases_ = ["ctx",]

    old_registers = {}

    def __init__(self):
        super(ContextCommand, self).__init__(prefix=False)
        self.add_setting("enable", True, "Enable/disable printing the context when breaking")
        self.add_setting("show_stack_raw", False, "Show the stack pane as raw hexdump (no dereference)")
        self.add_setting("show_registers_raw", True, "Show the registers pane with raw values (no dereference)")
        self.add_setting("nb_lines_stack", 8, "Number of line in the stack pane")
        self.add_setting("nb_lines_backtrace", 10, "Number of line in the backtrace pane")
        self.add_setting("nb_lines_code", 5, "Number of instruction before and after $pc")
        self.add_setting("ignore_registers", "", "Specify here a space-separated list of registers you do not here to display (for example: '$cs $ds $status')")
        self.add_setting("clear_screen", False, "Clear the screen before printing the context")

        self.add_setting("layout", "regs stack code source threads trace", "Change the order/display of the context")
        self.add_setting("redirect", "", "Redirect the context information to another TTY")

        if "capstone" in list(sys.modules.keys()):
            self.add_setting("use_capstone", False, "Use capstone as disassembler in the code pane (instead of GDB)")
        return

    def post_load(self):
        gdb.events.cont.connect(self.update_registers)
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if not self.get_setting("enable"):
            return

        current_layout = self.get_setting("layout").strip().split()
        if not current_layout:
            return

        self.tty_rows, self.tty_columns = get_terminal_size()
        layout_mapping = {
            "regs":  self.context_regs,
            "stack": self.context_stack,
            "code": self.context_code,
            "source": self.context_source,
            "trace": self.context_trace,
            "threads": self.context_threads,
        }

        redirect = self.get_setting("redirect")
        if redirect and os.access(redirect, os.W_OK):
            enable_redirect_output(to_file=redirect)

        if self.get_setting("clear_screen"):
            clear_screen(redirect)

        for pane in current_layout:
            if pane[0] in ("!", "-"):
                continue
            layout_mapping[pane]()

        self.context_title("")

        if redirect and os.access(redirect, os.W_OK):
            disable_redirect_output()
        return

    def context_title(self, m):
        line_color= __config__.get("theme.context_title_line")[0]
        msg_color = __config__.get("theme.context_title_message")[0]

        if not m:
            print(Color.colorify(horizontal_line * self.tty_columns, line_color))
            return

        trail_len = len(m) + 8
        title = ""
        title += Color.colorify("{:{padd}<{width}}[ ".format("",
                                                             width=self.tty_columns - trail_len,
                                                             padd=horizontal_line),
                                attrs=line_color)
        title += Color.colorify(m, msg_color)
        title += Color.colorify(" ]{:{padd}<4}".format("", padd=horizontal_line),
                                attrs=line_color)
        print(title)
        return

    def context_regs(self):
        self.context_title("registers")
        ignored_registers = set(self.get_setting("ignore_registers").split())

        if self.get_setting("show_registers_raw") == False:
            regs = set([x.strip() for x in current_arch.all_registers])
            printable_registers = " ".join(list(regs - ignored_registers))
            gdb.execute("registers {}".format(printable_registers))
            return

        l = max(map(len, current_arch.all_registers))
        l += 5
        l += 16 if is_elf64() else 8
        nb = get_terminal_size()[1]//l
        i = 1
        line = ""

        for reg in current_arch.all_registers:
            if reg.strip() in ignored_registers:
                continue

            try:
                r = gdb.parse_and_eval(reg)
                if r.type.code == gdb.TYPE_CODE_VOID:
                    continue

                new_value_type_flag = (r.type.code == gdb.TYPE_CODE_FLAGS)
                new_value = long(r)

            except (gdb.MemoryError, gdb.error):
                # If this exception is triggered, it means that the current register
                # is corrupted. Just use the register "raw" value (not eval-ed)
                new_value = get_register_ex(reg)
                new_value_type_flag = False

            except Exception:
                new_value = 0

            old_value = self.old_registers[reg] if reg in self.old_registers else 0x00

            line += "{:s}  ".format(Color.greenify(reg))
            if new_value_type_flag:
                line += "{:s} ".format(str(new_value))
            else:
                new_value = align_address(new_value)
                old_value = align_address(old_value)
                if new_value == old_value:
                    line += "{:s} ".format(format_address(new_value))
                else:
                    line += "{:s} ".format(Color.colorify(format_address(new_value), attrs="bold red"))

            if i % nb == 0 :
                print(line)
                line = ""
            i += 1

        if line:
            print(line)

        print("Flags: {:s}".format(current_arch.flag_register_to_human()))
        return

    def context_stack(self):
        self.context_title("stack")

        show_raw = self.get_setting("show_stack_raw")
        nb_lines = self.get_setting("nb_lines_stack")

        try:
            sp = current_arch.sp
            if show_raw == True:
                mem = read_memory(sp, 0x10 * nb_lines)
                print(hexdump(mem, base=sp))
            else:
                gdb.execute("dereference {:#x} {:d}".format(sp, nb_lines))

        except gdb.MemoryError:
            err("Cannot read memory from $SP (corrupted stack pointer?)")

        return

    def context_code(self):
        nb_insn = self.get_setting("nb_lines_code")
        use_capstone = self.has_setting("use_capstone") and self.get_setting("use_capstone")
        pc = current_arch.pc

        frame = gdb.selected_frame()
        arch = frame.architecture()
        arch_name = arch.name().lower()
        if is_arm_thumb():
            arch_name += ":thumb"
            pc   += 1

        self.context_title("code:{}".format(arch_name))

        try:
            if use_capstone:
                CapstoneDisassembleCommand.disassemble(pc, nb_insn)
                return

            for insn in gef_disassemble(pc, nb_insn):
                line = []
                is_branch = False
                is_taken  = False
                text = str(insn)

                if insn.address < pc:
                    line += Color.grayify(text)

                elif insn.address == pc:
                    line += Color.colorify("{:s}  {:s}$pc".format(text, left_arrow), attrs="bold red")

                    if current_arch.is_conditional_branch(insn):
                        is_taken, reason = current_arch.is_branch_taken(insn)
                        if is_taken:
                            reason = "[Reason: {:s}]".format(reason) if reason else ""
                            line += Color.colorify("\tTAKEN {:s}".format(reason), attrs="bold green")
                        else:
                            reason = "[Reason: !({:s})]".format(reason) if reason else ""
                            line += Color.colorify("\tNOT taken {:s}".format(reason), attrs="bold red")

                else:
                    line += text

                print("".join(line))

                if is_taken:
                    target = insn.operands[-1].split()[0]
                    target = int(target, 16)
                    for i, insn in enumerate(gef_disassemble(target, nb_insn, from_top=True)):
                        text= "{}  {}".format (down_arrow if i==0 else " ", str(insn))
                        print(text)
                    break

        except gdb.MemoryError:
            err("Cannot disassemble from $PC")
        return

    def context_source(self):
        try:
            pc = current_arch.pc
            symtabline = gdb.find_pc_line(pc)
            symtab = symtabline.symtab
            line_num = symtabline.line - 1     # we substract one because line number returned by gdb start at 1
            if not symtab.is_valid():
                return

            fpath = symtab.fullname()
            with open(fpath, "r") as f:
                lines = [l.rstrip() for l in f.readlines()]

        except Exception:
            return

        nb_line = self.get_setting("nb_lines_code")
        title = "source:{0:s}+{1:d}".format(symtab.filename, line_num + 1)
        self.context_title(title)

        for i in range(line_num - nb_line + 1, line_num + nb_line):
            if i < 0:
                continue

            if i < line_num:
                print(Color.grayify("{:4d}\t {:s}".format(i + 1, lines[i],)))

            if i == line_num:
                extra_info = self.get_pc_context_info(pc, lines[i])
                print(Color.colorify("{:4d}\t {:s} \t\t {:s} $pc\t".format(i + 1, lines[i], left_arrow,), attrs="bold red") + extra_info)

            if i > line_num:
                try:
                    print("{:4d}\t {:s}".format(i + 1, lines[i],))
                except IndexError:
                    break
        return

    def get_pc_context_info(self, pc, line):
        try:
            current_block = gdb.block_for_pc(pc)
            if not current_block.is_valid(): return ""
            m = []
            for sym in current_block:
                if not sym.is_function and sym.name in line:
                    key = sym.name
                    val = gdb.parse_and_eval(sym.name)
                    if val.type.code in (gdb.TYPE_CODE_PTR, gdb.TYPE_CODE_ARRAY):
                        addr = long(val.address)
                        addrs = DereferenceCommand.dereference_from(addr)
                        if len(addrs) > 2:
                            addrs = [addrs[0], "[...]", addrs[-1]]

                        f = " {:s} ".format(right_arrow)
                        val = f.join(addrs)
                    elif val.type.code == gdb.TYPE_CODE_INT:
                        val = hex(long(val))
                    else:
                        continue

                    found = any([k == key for k, v in m])
                    if not found:
                        m.append((key, val))

            if m:
                return "; " + ", ".join(["{:s}={:s}".format(Color.yellowify(a),b) for a, b in m])
        except Exception:
            pass
        return ""

    def context_trace(self):
        self.context_title("trace")

        nb_backtrace = self.get_setting("nb_lines_backtrace")
        orig_frame = current_frame = gdb.selected_frame()
        i = 0

        # backward compat for gdb (gdb < 7.10)
        if not hasattr(gdb, "FrameDecorator"):
            gdb.execute("backtrace {:d}".format(nb_backtrace))
            return

        while current_frame:
            current_frame.select()
            if not current_frame.is_valid():
                continue

            pc = current_frame.pc()
            name = current_frame.name()
            items = []
            items.append("RetAddr: {:#x}".format(pc))
            if name:
                frame_args = gdb.FrameDecorator.FrameDecorator(current_frame).frame_args() or []
                m = "Name: {:s}(".format(Color.greenify(name))
                m += ",".join(["{!s}={!s}".format(x.sym, x.sym.value(current_frame)) for x in frame_args])
                m += ")"
                items.append(m)

            print("[{:s}] {:s}".format(Color.colorify("#{:d}".format(i), "bold pink"),
                                        ", ".join(items)))
            current_frame = current_frame.older()
            i += 1
            nb_backtrace -= 1
            if nb_backtrace == 0:
                break

        orig_frame.select()
        return

    def context_threads(self):
        def reason():
            res = gdb.execute("info program", to_string=True).splitlines()
            if not res:
                return "NOT RUNNING"

            for line in res:
                line = line.strip()
                if line.startswith("It stopped with signal "):
                    return line.replace("It stopped with signal ", "").split(",", 1)[0]
                if  line == "The program being debugged is not being run.":
                    return "NOT RUNNING"
                if line == "It stopped at a breakpoint that has since been deleted.":
                    return "TEMPORARY BREAKPOINT"
                if line.startswith("It stopped at breakpoint "):
                    return "BREAKPOINT"
                if line == "It stopped after being stepped.":
                    return "SINGLE STEP"

            return "STOPPED"

        self.context_title("threads")

        threads = gdb.selected_inferior().threads()
        if not threads:
            warn("No thread selected")
            return

        i = 0
        for thread in threads:
            line = """[{:s}] Id {:d}, Name: "{:s}", """.format(Color.colorify("#{:d}".format(i), attrs="bold pink"),
                                                               thread.num, thread.name or "")
            if thread.is_running():
                line += Color.colorify("running", attrs="bold green")
            elif thread.is_stopped():
                line += Color.colorify("stopped", attrs="bold red")
                line += ", reason: {}".format(Color.colorify(reason(), attrs="bold pink"))
            elif thread.is_exited():
                line += Color.colorify("exited", attrs="bold yellow")
            print(line)
            i += 1
        return

    def update_registers(self, event):
        for reg in current_arch.all_registers:
            try:
                self.old_registers[reg] = get_register(reg)
            except Exception:
                self.old_registers[reg] = 0
        return



def disable_context():
    __config__["context.enable"][0] = False
    return


def enable_context():
    __config__["context.enable"][0] = True
    return


class HexdumpCommand(GenericCommand):
    """Display arranged hexdump (according to architecture endianness) of memory range."""

    _cmdline_ = "hexdump"
    _syntax_  = "{:s} (qword|dword|word|byte) LOCATION L[SIZE] [UP|DOWN]".format(_cmdline_)


    def post_load(self):
        GefAlias("dq", "hexdump qword")
        GefAlias("dd", "hexdump dword")
        GefAlias("dw", "hexdump word")
        GefAlias("dc", "hexdump byte")
        return

    @if_gdb_running
    def do_invoke(self, argv):
        argc = len(argv)
        if argc < 2:
            self.usage()
            return

        if argv[0] not in ("qword", "dword", "word", "byte"):
            self.usage()
            return

        fmt, argv = argv[0], argv[1:]
        read_from = align_address(long(gdb.parse_and_eval(argv[0])))
        read_len = 10
        up_to_down = True

        if argc >= 2:
            for arg in argv[1:]:
                if arg.startswith("L") or arg.startswith("l"):
                    if arg[1:].isdigit():
                        read_len = long(arg[1:])
                        continue

                if arg in ("UP", "Up", "up"):
                    up_to_down = True
                    continue

                if arg in ("DOWN", "Down", "down"):
                    up_to_down = False
                    continue

        if fmt == "byte":
            mem = read_memory(read_from, read_len)
            lines = hexdump(mem, base=read_from).splitlines()
        else:
            lines = self._hexdump(read_from, read_len, fmt)

        if not up_to_down:
            lines.reverse()

        print("\n".join(lines))
        return


    def _hexdump(self, start_addr, length, arrange_as):
        elf = get_elf_headers()
        if elf is None:
            return
        endianness = "<" if elf.e_endianness == Elf.LITTLE_ENDIAN else ">"
        i = 0

        formats = {
            "qword": ("Q", 8),
            "dword": ("I", 4),
            "word": ("H", 2),
        }

        r, l = formats[arrange_as]
        fmt_str = "%#x+%.4x {:s} %#.{:s}x".format(vertical_line, str(l * 2))
        fmt_pack = endianness + r
        lines = []

        while i < length:
            cur_addr = start_addr + i * l
            mem = read_memory(cur_addr, l)
            val = struct.unpack(fmt_pack, mem)[0]
            lines.append(fmt_str % (start_addr, i * l, val))
            i += 1

        return lines



class DereferenceCommand(GenericCommand):
    """Dereference recursively an address and display information"""

    _cmdline_ = "dereference"
    _syntax_  = "{:s} [LOCATION] [NB]".format(_cmdline_)
    _aliases_ = ["telescope", "dps",]


    def __init__(self):
        super(DereferenceCommand, self).__init__(complete=gdb.COMPLETE_LOCATION, prefix=False)
        self.add_setting("max_recursion", 7, "Maximum level of pointer recursion")
        return

    def pprint_dereferenced(self, addr, off):
        base_address_color = __config__.get("theme.dereference_base_address")[0]
        registers_color = __config__.get("theme.dereference_register_value")[0]

        regs = [(k.strip(), get_register_ex(k)) for k in current_arch.all_registers]
        sep = " {:s} ".format(right_arrow)
        memalign = get_memory_alignment()

        offset = off * memalign
        current_address = align_address(addr + offset)
        addrs = DereferenceCommand.dereference_from(current_address)
        l  = ""
        addr_l = format_address(long(addrs[0], 16))
        l += "{:s}{:s}+{:#04x}: {:s}".format(Color.colorify(addr_l, attrs=base_address_color),
                                             vertical_line, offset,
                                             sep.join(addrs[1:]))

        values = []
        for regname, regvalue in regs:
            if current_address == regvalue:
                values.append(regname)

        if values:
            m = "\t{:s}{:s}".format(left_arrow, ", ".join(list(values)))
            l += Color.colorify(m, attrs=registers_color)

        offset += memalign
        return l

    @if_gdb_running
    def do_invoke(self, argv):
        if len(argv) < 1:
            err("Missing location.")
            return

        nb = int(argv[1]) if len(argv) == 2 and argv[1].isdigit() else 1
        start_address = align_address(long(gdb.parse_and_eval(argv[0])))

        for i in range(0, nb):
            print(self.pprint_dereferenced(start_address, i))
        return


    @staticmethod
    def dereference_from(addr):
        if not is_alive():
            return [format_address(addr),]

        prev_addr_value = None
        max_recursion = max(int(__config__["dereference.max_recursion"][0]), 1)
        value = align_address(long(addr))
        addr = lookup_address(value)
        if not addr.valid or addr.value == 0x00:
            return [format_address(addr.value),]

        msg = []
        code_color = __config__.get("theme.dereference_code")[0]
        string_color = __config__.get("theme.dereference_string")[0]
        while max_recursion:
            if addr.value == prev_addr_value:
                msg.append("[loop detected]")
                break

            msg.append(format_address(addr.value))

            prev_addr_value = addr.value
            max_recursion -= 1

            # can we derefence more ?
            deref = addr.dereference()
            new_addr = lookup_address(deref)
            if new_addr.valid:
                addr = new_addr
                continue

            # otherwise try to parse the value
            if addr.section:
                if addr.section.is_executable() and addr.is_in_text_segment():
                    insn = gef_current_instruction(addr.value)
                    msg.append(Color.colorify(str(insn), attrs=code_color))
                    break

                elif addr.section.permission.value & Permission.READ:
                    if is_readable_string(addr.value):
                        s = read_cstring_from_memory(addr.value)
                        if len(s) < get_memory_alignment():
                            txt = '{:s} ("{:s}"?)'.format(format_address(deref), Color.colorify(s, attrs=string_color))
                        elif len(s) >= 50:
                            txt = Color.colorify('"{:s}[...]"'.format(s[:50]), attrs=string_color)
                        else:
                            txt = Color.colorify('"{:s}"'.format(s), attrs=string_color)

                        msg.append(txt)
                        break

            # if not able to parse cleanly, simply display and break
            val = "{:x}".format(long(deref) & 0xffffffffffffffff)
            if len(val)%2 != 0:  # pad the hexa representation to a multiple of 2
                val = "0"+val

            # if the value is only made of printable characters, display its value
            val_str = binascii.unhexlify(val)
            charset="""0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~ """
            if PYTHON_MAJOR==3:
                is_string = all(map(lambda x: chr(x) in charset, val_str))
            else:
                is_string = all(map(lambda x: x in charset, val_str))
            if is_string:
                val+= ' ("{}"?)'.format(Color.colorify(gef_pystring(val_str), attrs=string_color))
            msg.append("0x"+val)
            break

        return msg


class ASLRCommand(GenericCommand):
    """View/modify GDB ASLR behavior."""

    _cmdline_ = "aslr"
    _syntax_  = "{:s} (on|off)".format(_cmdline_)

    def do_invoke(self, argv):
        argc = len(argv)

        if argc == 0:
            ret = gdb.execute("show disable-randomization", to_string=True)
            i = ret.find("virtual address space is ")
            if i < 0:
                return

            msg = "ASLR is currently "
            if ret[i + 25:].strip() == "on.":
                msg += Color.redify("disabled")
            else:
                msg += Color.greenify("enabled")

            print(msg)
            return

        elif argc == 1:
            if argv[0] == "on":
                info("Enabling ASLR")
                gdb.execute("set disable-randomization off")
                return
            elif argv[0] == "off":
                info("Disabling ASLR")
                gdb.execute("set disable-randomization on")
                return

            warn("Invalid command")

        self.usage()
        return


class ResetCacheCommand(GenericCommand):
    """Reset cache of all stored data."""

    _cmdline_ = "reset-cache"
    _syntax_  = _cmdline_

    def do_invoke(self, argv):
        reset_all_caches()
        return


class VMMapCommand(GenericCommand):
    """Display virtual memory mapping"""

    _cmdline_ = "vmmap"
    _syntax_  = "{:s}".format(_cmdline_)

    @if_gdb_running
    def do_invoke(self, argv):
        vmmap = get_process_maps()
        if not vmmap:
            err("No address mapping information found")
            return

        color = __config__.get("theme.xinfo_title_message")[0]
        headers = [Color.colorify(x, attrs=color) for x in ["Start", "End", "Offset", "Perm", "Path"]]
        if is_elf64():
            print("{:<31s} {:<31s} {:<31s} {:<4s} {:s}".format(*headers))
        else:
            print("{:<23s} {:<23s} {:<23s} {:<4s} {:s}".format(*headers))

        for entry in vmmap:
            l = []
            l.append(format_address(entry.page_start))
            l.append(format_address(entry.page_end))
            l.append(format_address(entry.offset))

            if entry.permission.value == (Permission.READ|Permission.WRITE|Permission.EXECUTE) :
                l.append(Color.colorify(str(entry.permission), attrs="blink bold red"))
            else:
                l.append(str(entry.permission))

            l.append(entry.path)
            print(" ".join(l))
        return


class XFilesCommand(GenericCommand):
    """Shows all libraries (and sections) loaded by binary (Truth is out there)."""

    _cmdline_ = "xfiles"
    _syntax_  = "{:s} [name]".format(_cmdline_)

    @if_gdb_running
    def do_invoke(self, args):
        name = None if not args else args[0]
        formats = {"Start": "{:{align}20s}",
                   "End":   "{:{align}20s}",
                   "Name":  "{:{align}30s}",
                   "File":  "{:s}",
                  }
        args = ("Start", "End", "Name", "File")
        f = " ".join([formats[k] for k in args])
        print(f.format(*args, align="^"))

        for xfile in get_info_files():
            if name is not None and xfile.name != name:
                continue

            l= ""
            l += formats["Start"].format(format_address(xfile.zone_start), align=">")
            l += formats["End"].format(format_address(xfile.zone_end), align=">")
            l += formats["Name"].format(xfile.name, align="^")
            l += formats["File"].format(xfile.filename, align="<")
            print(l)
        return


class XAddressInfoCommand(GenericCommand):
    """Get virtual section information for specific address"""

    _cmdline_ = "xinfo"
    _syntax_  = "{:s} LOCATION".format(_cmdline_)


    def __init__(self):
        super(XAddressInfoCommand, self).__init__(complete=gdb.COMPLETE_LOCATION)
        return

    @if_gdb_running
    def do_invoke (self, argv):
        if len(argv) == 0:
            err ("At least one valid address must be specified")
            self.usage()
            return

        for sym in argv:
            try:
                addr = align_address(parse_address(sym))
                print(titlify("xinfo: {:#x}".format(addr)))
                self.infos(addr)

            except gdb.error as gdb_err:
                err("{:s}".format(gdb_err))
        return


    def infos(self, address):
        addr = lookup_address(address)
        if not addr.valid:
            warn("Cannot reach {:#x} in memory space".format(address))
            return

        sect = addr.section
        info = addr.info

        if sect:
            print("Found {:s}".format(format_address(addr.value)))
            print("Page: {:s} {:s} {:s} (size={:#x})".format(format_address(sect.page_start),
                                                             right_arrow,
                                                             format_address(sect.page_end),
                                                             sect.page_end-sect.page_start))
            print("Permissions: {:s}".format(str(sect.permission)))
            print("Pathname: {:s}".format(sect.path))
            print("Offset (from page): +{:#x}".format(addr.value-sect.page_start))
            print("Inode: {:s}".format(sect.inode))

        if info:
            print("Segment: {:s} ({:s}-{:s})".format(info.name,
                                                     format_address(info.zone_start),
                                                     format_address(info.zone_end)))
        return


class XorMemoryCommand(GenericCommand):
    """XOR a block of memory."""

    _cmdline_ = "xor-memory"
    _syntax_  = "{:s} <display|patch> <address> <size_to_read> <xor_key> ".format(_cmdline_)


    def do_invoke(self, argv):
        if len(argv) == 0:
            err("Missing subcommand <display|patch>")
            self.usage()
        return


class XorMemoryDisplayCommand(GenericCommand):
    """Display a block of memory by XOR-ing each key with a key."""

    _cmdline_ = "xor-memory display"
    _syntax_  = "{:s} <address> <size_to_read> <xor_key> [-i]".format(_cmdline_)

    @if_gdb_running
    def do_invoke(self, argv):
        if len(argv) not in (3, 4):
            self.usage()
            return

        address = long(gdb.parse_and_eval(argv[0]))
        length = long(argv[1], 0)
        key = argv[2]
        show_as_instructions = True if len(argv) == 4 and argv[3] == "-i" else False
        block = read_memory(address, length)
        info("Displaying XOR-ing {:#x}-{:#x} with {:s}".format(address, address + len(block), repr(key)))

        print(titlify("Original block"))
        if show_as_instructions:
            CapstoneDisassembleCommand.disassemble(address, -1, code=block)
        else:
            print(hexdump(block, base=address))


        print(titlify("XOR-ed block"))
        xored = xor(block, key)
        if show_as_instructions:
            CapstoneDisassembleCommand.disassemble(address, -1, code=xored)
        else:
            print(hexdump(xored, base=address))
        return


class XorMemoryPatchCommand(GenericCommand):
    """Patch a block of memory by XOR-ing each key with a key."""

    _cmdline_ = "xor-memory patch"
    _syntax_  = "{:s} <address> <size_to_read> <xor_key>".format(_cmdline_)

    @if_gdb_running
    def do_invoke(self, argv):
        if len(argv) != 3:
            self.usage()
            return

        address = parse_address(argv[0])
        length = long(argv[1], 0)
        key = argv[2]
        block = read_memory(address, length)
        info("Patching XOR-ing {:#x}-{:#x} with '{:s}'".format(address, address + len(block), key))

        xored_block = xor(block, key)
        write_memory(address, xored_block, length)
        return


class TraceRunCommand(GenericCommand):
    """Create a runtime trace of all instructions executed from $pc to LOCATION specified."""

    _cmdline_ = "trace-run"
    _syntax_  = "{:s} LOCATION [MAX_CALL_DEPTH]".format(_cmdline_)


    def __init__(self):
        super(TraceRunCommand, self).__init__(self._cmdline_, complete=gdb.COMPLETE_LOCATION)
        self.add_setting("max_tracing_recursion", 1, "Maximum depth of tracing")
        self.add_setting("tracefile_prefix", "./gef-trace-", "Specify the tracing output file prefix")
        return

    @if_gdb_running
    def do_invoke(self, argv):
        if len(argv) not in (1, 2):
            self.usage()
            return

        if len(argv) == 2 and argv[1].isdigit():
            depth = long(argv[1])
        else:
            depth = 1

        try:
            loc_start   = current_arch.pc
            loc_end     = long(gdb.parse_and_eval(argv[0]))
        except gdb.error as e:
            err("Invalid location: {:s}".format(e))
            return

        self.trace(loc_start, loc_end, depth)
        return


    def get_frames_size(self):
        n = 0
        f = gdb.newest_frame()
        while f:
            n += 1
            f = f.older()
        return n


    def trace(self, loc_start, loc_end, depth):
        info("Tracing from {:#x} to {:#x} (max depth={:d})".format(loc_start, loc_end,depth))
        logfile = "{:s}{:#x}-{:#x}.txt".format(self.get_setting("tracefile_prefix"), loc_start, loc_end)

        enable_redirect_output(to_file=logfile)
        disable_context()

        self._do_trace(loc_start, loc_end, depth)

        enable_context()
        disable_redirect_output()

        ok("Done, logfile stored as '{:s}'".format(logfile))
        info("Hint: import logfile with `ida_color_gdb_trace.py` script in IDA to visualize path")
        return


    def _do_trace(self, loc_start, loc_end, depth):
        loc_cur = loc_start
        frame_count_init = self.get_frames_size()

        print("#")
        print("# Execution tracing of {:s}".format(get_filepath()))
        print("# Start address: {:s}".format(format_address(loc_start)))
        print("# End address: {:s}".format(format_address(loc_end)))
        print("# Recursion level: {:d}".format(depth))
        print("# automatically generated by gef.py")
        print("#\n")

        while loc_cur != loc_end:
            try:
                delta = self.get_frames_size() - frame_count_init

                if delta <= depth :
                    gdb.execute("stepi")
                else:
                    gdb.execute("finish")

                loc_cur = current_arch.pc
                gdb.flush()

            except Exception as e:
                print("#")
                print("# Execution interrupted at address {:s}".format(format_address(loc_cur)))
                print("# Exception: {:s}".format(e))
                print("#\n")
                break

        return


class PatternCommand(GenericCommand):
    """This command will create or search a De Bruijn cyclic pattern to facilitate
    determining the offset in memory. The algorithm used is the same as the one
    used by pwntools, and can therefore be used in conjunction.
    """

    _cmdline_ = "pattern"
    _syntax_  = "{:s} (create|search) <args>".format(_cmdline_)

    def __init__(self, *args, **kwargs):
        super(PatternCommand, self).__init__()
        self.add_setting("length", 1024, "Initial length of a cyclic buffer to generate")
        return

    def do_invoke(self, argv):
        self.usage()
        return


class PatternCreateCommand(GenericCommand):
    """Cyclic pattern generation"""

    _cmdline_ = "pattern create"
    _syntax_  = "{:s} [SIZE]".format(_cmdline_)


    def do_invoke(self, argv):
        if len(argv) == 1:
            if not argv[0].isdigit():
                err("Invalid size")
                return
            __config__["pattern.length"][0] = long(argv[0])
        elif len(argv) > 1:
            err("Invalid syntax")
            return

        size = __config__.get("pattern.length", 1024)[0]
        info("Generating a pattern of {:d} bytes".format(size))
        patt = generate_cyclic_pattern(size).decode("utf-8")
        if size < 1024:
            print(patt)

        var_name = gef_convenience('"{:s}"'.format(patt))
        ok("Saved as '{:s}'".format(var_name))
        return


class PatternSearchCommand(GenericCommand):
    """Cyclic pattern search"""

    _cmdline_ = "pattern search"
    _syntax_  = "{:s} PATTERN [SIZE]".format(_cmdline_)


    def do_invoke(self, argv):
        if len(argv) not in (1, 2):
            self.usage()
            return

        if len(argv) == 2:
            if not argv[0].isdigit():
                err("Invalid size")
                return
            size = long(argv[1])
        else:
            size = __config__.get("pattern.length", 1024)[0]

        pattern = argv[0]
        info("Searching '{:s}'".format(pattern))
        self.search(pattern, size)
        return

    def search(self, pattern, size):
        try:
            addr = long(gdb.parse_and_eval(pattern))
            if get_memory_alignment(in_bits=True) == 32:
                pattern_be = struct.pack(">I", addr)
                pattern_le = struct.pack("<I", addr)
            else:
                pattern_be = struct.pack(">Q", addr)
                pattern_le = struct.pack("<Q", addr)
        except gdb.error:
            err("Incorrect pattern")
            return

        buf = generate_cyclic_pattern(size)
        found = False

        off = buf.find(pattern_le)
        if off >= 0:
            ok("Found at offset {:d} (little-endian search) {:s}".format(off, Color.colorify("likely", attrs="bold red") if is_little_endian() else ""))
            found = True

        off = buf.find(pattern_be)
        if off >= 0:
            ok("Found at offset {:d} (big-endian search) {:s}".format(off, Color.colorify("likely", attrs="bold green") if is_big_endian() else ""))
            found = True

        if not found:
            err("Pattern not found")
        return


class ChecksecCommand(GenericCommand):
    """Checksec.sh (http://www.trapkit.de/tools/checksec.html) port."""

    _cmdline_ = "checksec"
    _syntax_  = "{:s} (filename)".format(_cmdline_)


    def __init__(self):
        super(ChecksecCommand, self).__init__(complete=gdb.COMPLETE_FILENAME)
        return

    def pre_load(self):
        which("readelf")
        return

    def do_invoke(self, argv):
        argc = len(argv)

        if argc == 0:
            filename = get_filepath()
            if filename is None:
                warn("No executable/library specified")
                return
        elif argc == 1:
            filename = argv[0]
        else:
            self.usage()
            return

        info("{:s} for '{:s}'".format(self._cmdline_, filename))
        self.print_security_properties(filename)
        return

    def print_security_properties(self, filename):
        sec = checksec(filename)
        for prop in sec:
            val = sec[prop]
            msg = Color.greenify("Yes") if val is True else Color.redify("No")
            print("{:<30s}: {:s}".format(prop, msg))
        return


class FormatStringSearchCommand(GenericCommand):
    """Exploitable format-string helper: this command will set up specific breakpoints
    at well-known dangerous functions (printf, snprintf, etc.), and check if the pointer
    holding the format string is writable, and therefore susceptible to format string
    attacks if an attacker can control its content."""
    _cmdline_ = "format-string-helper"
    _syntax_ = "{:s}".format(_cmdline_)
    _aliases_ = ["fmtstr-helper",]


    def do_invoke(self, argv):
        dangerous_functions = {
            "printf": 0,
            "sprintf": 1,
            "fprintf": 1,
            "snprintf": 2,
            "vsnprintf": 2,
        }

        enable_redirect_output("/dev/null")

        for func_name, num_arg in dangerous_functions.items():
            FormatStringBreakpoint(func_name, num_arg)

        disable_redirect_output()
        ok("Enabled {:d} FormatStringBreakpoint".format(len(dangerous_functions)))
        return


class GefCommand(gdb.Command):
    """GEF main command: view all new commands by typing `gef`"""

    _cmdline_ = "gef"
    _syntax_  = "{:s} (help|missing|config|save|restore|set|run)".format(_cmdline_)

    def __init__(self):
        super(GefCommand, self).__init__(GefCommand._cmdline_,
                                         gdb.COMMAND_SUPPORT,
                                         gdb.COMPLETE_NONE,
                                         True)

        __config__["gef.follow_child"] = [True, bool, "Automatically set GDB to follow child when forking"]
        __config__["gef.readline_compat"] = [False, bool, "Workaround for readline SOH/ETX issue (SEGV)"]
        __config__["gef.debug"] = [False, bool, "Enable debug mode for gef"]
        __config__["gef.autosave_breakpoints_file"] = ["", str, "Automatically save and restore breakpoints"]

        self.classes = [
            GefThemeCommand,
            ResetCacheCommand,
            XAddressInfoCommand,
            XorMemoryCommand, XorMemoryDisplayCommand, XorMemoryPatchCommand,
            FormatStringSearchCommand,
            TraceRunCommand,
            PatternCommand, PatternSearchCommand, PatternCreateCommand,
            ChecksecCommand,
            VMMapCommand,
            XFilesCommand,
            ASLRCommand,
            DereferenceCommand,
            HexdumpCommand,
            CapstoneDisassembleCommand,
            ContextCommand,
            EntryPointBreakCommand,
            ElfInfoCommand,
            ProcessListingCommand,
            AssembleCommand,
            ROPgadgetCommand,
            RopperCommand,
            ShellcodeCommand, ShellcodeSearchCommand, ShellcodeGetCommand,
            DetailRegistersCommand,
            SolveKernelSymbolCommand,
            GlibcHeapCommand, GlibcHeapArenaCommand, GlibcHeapChunkCommand, GlibcHeapBinsCommand, GlibcHeapFastbinsYCommand, GlibcHeapUnsortedBinsCommand, GlibcHeapSmallBinsCommand, GlibcHeapLargeBinsCommand,
            NopCommand,
            StubCommand,
            RemoteCommand,
            UnicornEmulateCommand,
            ChangePermissionCommand,
            FlagsCommand,
            SearchPatternCommand,
            IdaInteractCommand,
            ChangeFdCommand,
            RetDecCommand,
            PCustomCommand,
            ProcessStatusCommand,
            CanaryCommand,
            # add new commands here
            # when subcommand, main command must be placed first
            ]

        self.__cmds = [(x._cmdline_, x) for x in self.classes]
        self.__loaded_cmds = []
        self.load()

        # loading GEF sub-commands
        GefHelpCommand(self.__loaded_cmds)
        GefConfigCommand(self.loaded_command_names)
        GefSaveCommand()
        GefRestoreCommand()
        GefMissingCommand()
        GefSetCommand()
        GefRunCommand()

        # restore saved settings (if any)
        if os.access(GEF_RC, os.R_OK):
            gdb.execute("gef restore")

        # restore the follow-fork-mode policy
        if __config__.get("gef.follow_child")[0]:
            gdb.execute("set follow-fork-mode child")
        else:
            gdb.execute("set follow-fork-mode parent")

        # restore the autosave/autoreload breakpoints policy (if any)
        bkp_fname = __config__.get("gef.autosave_breakpoints_file")[0]
        if bkp_fname:
            # restore if existing
            if os.access(bkp_fname, os.R_OK):
                gdb.execute("source {:s}".format(bkp_fname))

            # add hook for autosave breakpoints on quit command
            source = [
                "define hook-quit",
                " save breakpoints {:s}".format(bkp_fname),
                "end"
            ]
            gef_execute_gdb_script("\n".join(source) + "\n")

        return


    @property
    def loaded_command_names(self):
        return [x[0] for x in self.__loaded_cmds]


    def invoke(self, args, from_tty):
        self.dont_repeat()
        gdb.execute("gef help")
        return


    def load(self, mod=None):
        """
        Load all the commands defined by GEF into GDB.
        If a configuration file is found, the settings are restored.
        """
        global __loaded__, __missing__

        __loaded__ = []
        __missing__ = {}
        nb_missing = 0

        def is_loaded(x):
            return any(filter(lambda u: x == u[0], __loaded__))

        for cmd, class_name in self.__cmds:
            try:
                if " " in cmd:
                    # if subcommand, check root command is loaded
                    root = cmd.split(" ", 1)[0]
                    if not is_loaded(root):
                        continue

                __loaded__.append((cmd, class_name, class_name()))

                if hasattr(class_name, "_aliases_"):
                    aliases = getattr(class_name, "_aliases_")
                    for alias in aliases:
                        GefAlias(alias, cmd)

            except Exception as reason:
                __missing__[cmd] = reason
                nb_missing += 1

        self.__loaded_cmds = sorted(__loaded__, key=lambda x: x[1]._cmdline_)

        print("{:s} for {:s} ready, type `{:s}' to start, `{:s}' to configure".format(Color.greenify("GEF"),
                                                                                      get_os(),
                                                                                      Color.colorify("gef",attrs="underline yellow"),
                                                                                      Color.colorify("gef config", attrs="underline pink")))

        ver = "{:d}.{:d}".format(sys.version_info.major, sys.version_info.minor)
        nb_cmds = len(__loaded__)
        print("{:s} commands loaded for GDB {:s} using Python engine {:s}".format(Color.colorify(str(nb_cmds), attrs="bold green"),
                                                                                  Color.colorify(gdb.VERSION, attrs="bold yellow"),
                                                                                  Color.colorify(ver, attrs="bold red")))

        if nb_missing > 0:
            warn("{:s} commands could not be loaded, run `{:s}` to know why.".format(Color.colorify(str(nb_missing), attrs="bold red"),
                                                                                     Color.colorify("gef missing", attrs="underline pink")))
        return


class GefHelpCommand(gdb.Command):
    """GEF help sub-command."""
    _cmdline_ = "gef help"
    _syntax_  = _cmdline_

    def __init__(self, commands, *args, **kwargs):
        super(GefHelpCommand, self).__init__(GefHelpCommand._cmdline_,
                                             gdb.COMMAND_SUPPORT,
                                             gdb.COMPLETE_NONE,
                                             False)
        self.__doc__ = self.generate_help(commands)
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        print(titlify("GEF - GDB Enhanced Features"))
        print(self.__doc__)
        return

    def generate_help(self, commands):
        d = []

        for cmd, class_name, _ in commands:
            if " " in cmd:
                # do not print out subcommands in main help
                continue

            doc = class_name.__doc__ if hasattr(class_name, "__doc__") else ""
            doc = "\n                         ".join(doc.split("\n"))

            if hasattr(class_name, "_aliases_"):
                aliases = "(alias: {:s})".format(", ".join(class_name._aliases_))
            else:
                aliases = ""

            msg = "{:<25s} -- {:s} {:s}".format(cmd, Color.greenify(doc), aliases)

            d.append(msg)
        return "\n".join(d)


class GefConfigCommand(gdb.Command):
    """GEF configuration sub-command
    This command will help set/view GEF settingsfor the current debugging session.
    It is possible to make those changes permanent by running `gef save` (refer
    to this command help), and/or restore previously saved settings by running
    `gef restore` (refer help).
    """
    _cmdline_ = "gef config"
    _syntax_  = "{:s} [debug_on|debug_off][setting_name] [setting_value]".format(_cmdline_)

    def __init__(self, loaded_commands, *args, **kwargs):
        super(GefConfigCommand, self).__init__(GefConfigCommand._cmdline_,
                                               gdb.COMMAND_SUPPORT,
                                               prefix=False)
        self.loaded_commands = loaded_commands
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        argv = gdb.string_to_argv(args)
        argc = len(argv)

        if not (0 <= argc <= 2):
            err("Invalid number of arguments")
            return

        if argc == 0:
            print(titlify("GEF configuration settings"))
            self.print_settings()
            return

        if argc == 1:
            plugin_name = argv[0]
            print(titlify("GEF configuration setting: {:s}".format(plugin_name)))
            self.print_setting(plugin_name)
            return

        self.set_setting(argc, argv)
        return

    def print_setting(self, plugin_name):
        res = __config__.get(plugin_name)
        if res is not None:
            _value, _type, desc = res
            print("{:<35s}  ({:<5s}) = {:<50s}   {:s}".format(plugin_name,
                                                              _type.__name__,
                                                              str(_value),
                                                              Color.greenify(desc)))
        return

    def print_settings(self):
        for x in sorted(__config__):
            self.print_setting(x)
        return

    def set_setting(self, argc, argv):
        if "." not in argv[0]:
            err("Invalid command format")
            return

        plugin_name = argv[0].split(".", 1)[0]

        if plugin_name not in self.loaded_commands + ["gef"]:
            err("Unknown plugin '{:s}'".format(plugin_name))
            return

        _curval, _type, _desc = __config__.get(argv[0], [None, None, None])
        if _type is None:
            err("Failed to get '{:s}' config setting".format(argv[0],))
            return

        try:
            if _type == bool:
                _newval = True if argv[1].upper() in ("TRUE", "T", "1") else False
            else:
                _newval = _type(argv[1])

        except Exception:
            err("{} expects type '{}'".format(argv[0], _type.__name__))
            return

        __config__[argv[0]][0] = _newval
        return

    def complete(self, text, word):
        valid_settings = sorted(__config__)

        if text:
            return valid_settings

        completion = []
        for setting in valid_settings:
            if setting.startswith(text):
                completion.append(setting)

        if len(completion) == 1:
            if "." not in text:
                return completion

            choice = completion[0]
            i = choice.find(".") + 1
            return [choice[i:]]

        return completion


class GefSaveCommand(gdb.Command):
    """GEF save sub-command
    Saves the current configuration of GEF to disk (by default in file '~/.gef.rc')
    """
    _cmdline_ = "gef save"
    _syntax_  = _cmdline_

    def __init__(self, *args, **kwargs):
        super(GefSaveCommand, self).__init__(GefSaveCommand._cmdline_,
                                             gdb.COMMAND_SUPPORT,
                                             gdb.COMPLETE_NONE,
                                             False)
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        cfg = configparser.RawConfigParser()
        old_sect = None

        # save the configuration
        for key in sorted(__config__):
            sect, optname = key.split(".", 1)
            value = __config__.get(key, None)[0]

            if old_sect != sect:
                cfg.add_section(sect)
                old_sect = sect

            cfg.set(sect, optname, value)

        # save the aliases
        cfg.add_section("aliases")
        for alias in __aliases__:
            cfg.set("aliases", alias._alias, alias._command)

        with open(GEF_RC, "w") as fd:
            cfg.write(fd)

        ok("Configuration saved to '{:s}'".format(GEF_RC))
        return


class GefRestoreCommand(gdb.Command):
    """GEF restore sub-command
    Loads settings from file '~/.gef.rc' and apply them to the configuration of GEF
    """
    _cmdline_ = "gef restore"
    _syntax_  = _cmdline_

    def __init__(self, *args, **kwargs):
        super(GefRestoreCommand, self).__init__(GefRestoreCommand._cmdline_,
                                                gdb.COMMAND_SUPPORT,
                                                gdb.COMPLETE_NONE,
                                                False)
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        cfg = configparser.ConfigParser()
        cfg.read(GEF_RC)

        if not cfg.sections():
            return

        for section in cfg.sections():
            if section == "aliases":
                # load the aliases
                for key in cfg.options(section):
                    GefAlias(key, cfg.get(section, key))
                continue

            # load the other options
            for optname in cfg.options(section):
                try:
                    key = "{:s}.{:s}".format(section, optname)
                    _, _type, _ = __config__.get(key)
                    new_value = cfg.get(section, optname)
                    if _type == bool:
                        new_value = True if new_value == "True" else False
                    else:
                        new_value = _type(new_value)
                    __config__[key][0] = new_value
                except Exception:
                    pass

        ok("Configuration from '{:s}' restored".format(GEF_RC))
        return


class GefMissingCommand(gdb.Command):
    """GEF missing sub-command
    Display the GEF commands that could not be loaded, along with the reason of why
    they could not be loaded.
    """
    _cmdline_ = "gef missing"
    _syntax_  = _cmdline_

    def __init__(self, *args, **kwargs):
        super(GefMissingCommand, self).__init__(GefMissingCommand._cmdline_,
                                                gdb.COMMAND_SUPPORT,
                                                gdb.COMPLETE_NONE,
                                                False)
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        missing_commands = __missing__.keys()
        if not missing_commands:
            ok("No missing command")
            return

        for missing_command in missing_commands:
            reason = __missing__[missing_command]
            warn("Command `{}` is missing, reason {} {}".format(missing_command, right_arrow, reason))
        return


class GefSetCommand(gdb.Command):
    """Override GDB set commands with the context from GEF.
    """
    _cmdline_ = "gef set"
    _syntax_  = "{:s} [GDB_SET_ARGUMENTS]".format(_cmdline_)

    def __init__(self, *args, **kwargs):
        super(GefSetCommand, self).__init__(GefSetCommand._cmdline_,
                                            gdb.COMMAND_SUPPORT,
                                            gdb.COMPLETE_SYMBOL,
                                            False)
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        args = args.split()
        cmd = ["set", args[0],]
        for p in args[1:]:
            if p.startswith("$_gef"):
                c = gdb.parse_and_eval(p)
                cmd.append(c.string())
            else:
                cmd.append(p)

        gdb.execute(" ".join(cmd))
        return


class GefRunCommand(gdb.Command):
    """Override GDB run commands with the context from GEF.
    Simple wrapper for GDB run command to use arguments set from `gef set args`. """
    _cmdline_ = "gef run"
    _syntax_  = "{:s} [GDB_RUN_ARGUMENTS]".format(_cmdline_)

    def __init__(self, *args, **kwargs):
        super(GefRunCommand, self).__init__(GefRunCommand._cmdline_,
                                            gdb.COMMAND_SUPPORT,
                                            gdb.COMPLETE_FILENAME,
                                            False)
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        if is_alive():
            gdb.execute("continue")
            return

        argv = args.split()
        gdb.execute("gef set args {:s}".format(" ".join(argv)))
        gdb.execute("run")
        return


class GefAlias(gdb.Command):
    """Simple aliasing wrapper because GDB doesn't do what it should.
    """
    def __init__(self, alias, command):
        p = command.split()
        if not p:
            return

        if list(filter(lambda x: x._alias == alias, __aliases__)):
            return

        self._command = command
        self._alias = alias
        c = command.split()[0]
        r = self.lookup_command(c)
        self.__doc__ = "Alias for '{}'".format(Color.greenify(command))
        if r is not None:
            _name, _class, _instance = r
            self.__doc__ += ": {}".format(_instance.__doc__)

            if hasattr(_instance,  "complete"):
                self.complete = _instance.complete

        super(GefAlias, self).__init__(alias, gdb.COMMAND_NONE)
        __aliases__.append(self)
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        gdb.execute("{} {}".format(self._command, args), from_tty=from_tty)
        return

    def lookup_command(self, cmd):
        for _name, _class, _instance in __loaded__:
            if cmd == _name:
                return _name, _class, _instance

        return None


class GefAliases(gdb.Command):
    """List all custom aliases."""
    def __init__(self):
        super(GefAliases, self).__init__("aliases", gdb.COMMAND_OBSCURE, gdb.COMPLETE_NONE)
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()
        ok("Aliases defined:")
        for _alias in __aliases__:
            print("{:30s} {} {}".format(_alias._alias, right_arrow, _alias._command))
        return


class GefTmuxSetup(gdb.Command):
    """Setup a confortable tmux debugging environment."""
    def __init__(self):
        super(GefTmuxSetup, self).__init__("tmux-setup", gdb.COMMAND_NONE, gdb.COMPLETE_NONE)
        GefAlias("screen-setup", "tmux-setup")
        return

    def invoke(self, args, from_tty):
        self.dont_repeat()

        tmux = os.getenv("TMUX")
        if tmux:
            self.tmux_setup()
            return

        screen = os.getenv("TERM")
        if screen is not None and screen == "screen":
            self.screen_setup()
            return

        warn("Not in a tmux/screen session")
        return


    def tmux_setup(self):
        """Prepare the tmux environment by vertically splitting the current pane, and
        forcing the context to be redirected there."""
        tmux = which("tmux")
        ok("tmux session found, splitting window...")
        old_ptses = set(os.listdir("/dev/pts"))
        gdb.execute("! {} split-window -h 'clear ; cat'".format(tmux))
        gdb.execute("! {} select-pane -L".format(tmux))
        new_ptses = set(os.listdir("/dev/pts"))
        pty = list(new_ptses - old_ptses)[0]
        pty = "/dev/pts/{}".format(pty)
        ok("Setting `context.redirect` to '{}'...".format(pty))
        gdb.execute("gef config context.redirect {}".format(pty))
        ok("Done!")
        return


    def screen_setup(self):
        """Hackish equivalent of the tmux_setup() function for screen."""
        screen = which("screen")
        sty = os.getenv("STY")
        ok("screen session found, splitting window...")
        fd_script, script_path = tempfile.mkstemp()
        fd_tty, tty_path = tempfile.mkstemp()
        os.close(fd_tty)

        with os.fdopen(fd_script, "w") as f:
            f.write("startup_message off\n")
            f.write("split -v\n")
            f.write("focus right\n")
            f.write("screen /bin/bash -c 'tty > {}; clear; cat'\n".format(tty_path))
            f.write("focus left\n")

        gdb.execute("""! {} -r {} -m -d -X source {}""".format(screen, sty, script_path))
        # artificial delay to make sure `tty_path` is populated
        time.sleep(0.25)
        with open(tty_path, "r") as f:
            pty = f.read().strip()
        ok("Setting `context.redirect` to '{}'...".format(pty))
        gdb.execute("gef config context.redirect {}".format(pty))
        gdb.execute("set height 0")
        ok("Done!")
        os.unlink(script_path)
        os.unlink(tty_path)
        return


def __gef_prompt__(current_prompt):
    if __config__.get("gef.readline_compat")[0]:
        return gef_prompt

    if is_alive():
        return gef_prompt_on

    return gef_prompt_off


if __name__  == "__main__":

    # setup prompt
    gdb.prompt_hook = __gef_prompt__

    # setup config
    gdb.execute("set confirm off")
    gdb.execute("set verbose off")
    gdb.execute("set height 0")
    gdb.execute("set width 0")
    gdb.execute("set step-mode on")

    # gdb history
    gdb.execute("set history save on")
    gdb.execute("set history filename ~/.gdb_history")

    # gdb input and output bases
    gdb.execute("set output-radix 0x10")

    # pretty print
    gdb.execute("set print pretty on")

    try:
        # this will raise a gdb.error unless we're on x86
        # we can safely ignore this
        gdb.execute("set disassembly-flavor intel")
    except gdb.error:
        pass

    # SIGALRM will simply display a message, but gdb won't forward the signal to the process
    gdb.execute("handle SIGALRM print nopass")

    # saving GDB indexes in GEF tempdir
    gef_makedirs(GEF_TEMP_DIR)
    gdb.execute("save gdb-index {}".format(GEF_TEMP_DIR))

    # load GEF
    GefCommand()

    # gdb events configuration
    gdb.events.cont.connect(continue_handler)
    gdb.events.stop.connect(hook_stop_handler)
    gdb.events.new_objfile.connect(new_objfile_handler)
    gdb.events.exited.connect(exit_handler)

    GefAliases()
    GefTmuxSetup()
