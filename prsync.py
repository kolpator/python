#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# Copyright 2017 Jean-Baptiste Denis <jbd@jbdenis.net>

# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.
#
# This file includes a copy of the BSD licensed options.py file from the bup project
# See https://github.com/bup/bup/blob/master/lib/bup/options.py

VERSION = '20170730'

"""
Simple program from a very specific need. I needed to transfer multiple
terabytes of data using rsync. I can use multiple rsync in parallel to
improve the throughput but i didn't want to think about what data each rsync
should transfer, that's why i wrote this program.

I'm targeting a single file self sufficient python 2.6 program. Why 2.6 ? Because RHEL6 and
derivated. So please be indulgent regarding the pyton code. But feel free to make suggestions
to improve it in ways that keep it compatible with python 2.6.

This script build files lists to feed rsync with. It builds files lists whose total
disk size is below a provided limit and whose total number is below a provided limit.

Inspired by the fpsync tool from the fpart project. You should have a look since fpsync
is much more powerful right now and has been used to move around terabytes
of data already. See https://github.com/martymac/fpart
"""

# TODO
# - handle remote-shell src or dest dir, like a normal rsync
# - verbose, debug, multiprocessing compliant output

DEFAULT_RSYNC_OPTIONS = "-aS --numeric-ids"

MSRSYNC_OPTSPEC = """
msrsync [options] [--rsync "rsync-options-string"] SRCDIR [SRCDIR2...] DESTDIR
msrsync --selftest
--
 msrsync options:
p,processes=   number of rsync processes to use [1]
f,files=       limit buckets to <files> files number [1000]
s,size=        limit partitions to BYTES size (1024 suffixes: K, M, G, T, P, E, Z, Y) [1G]
b,buckets=     where to put the buckets files (default: auto temporary directory)
k,keep         do not remove buckets directory at the end
j,show         show bucket directory
P,progress     show progress
stats          show additional stats
d,dry-run      do not run rsync processes
v,version      print version
 rsync options:
r,rsync=       MUST be last option. rsync options as a quoted string ["%s"]. The "--from0 --files-from=... --quiet --verbose --stats --log-file=..." options will ALWAYS be added, no matter what. Be aware that this will affect all rsync *from/filter files if you want to use them. See rsync(1) manpage for details.
 self-test options:
t,selftest     run the integrated unit and functional tests
e,bench        run benchmarks
g,benchshm     run benchmarks in /dev/shm or the directory in $SHM environment variable
""" % DEFAULT_RSYNC_OPTIONS


RSYNC_EXE = None

EOPTION_PARSER = 97
EPYTHON_VERSION = 10
EBUCKET_DIR_NOEXIST = 11
EBUCKET_DIR_PERMS = 12
EBUCKET_DIR_OSERROR = 12
EBUCKET_FILE_CREATE = 13
EBIN_NOTFOUND = 14
ESRC_NOT_DIR = 15
ESRC_NO_ACCESS = 16
EDEST_NO_ACCESS = 17
EDEST_NOT_DIR = 18
ERSYNC_OPTIONS_CHECK = 19
ERSYNC_TOO_LONG = 20
ERSYNC_JOB = 21
ERSYNC_OK = 22
EDEST_IS_FILE = 23
EDEST_CREATE = 24
ENEED_ROOT = 25
EBENCH = 26
EMSRSYNC_INTERRUPTED = 27

TYPE_RSYNC = 0
TYPE_RSYNC_SENTINEL = 1
MSG_STDERR = 10
MSG_STDOUT = 11
MSG_PROGRESS = 12

# pylint: disable=wrong-import-position

import datetime
import gzip
import itertools
import multiprocessing
import os
import platform
import random
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import timeit
import traceback
import unittest

STDOUT_ENCODING = sys.stdout.encoding if None else 'utf8'

def _e(value):
    # pylint: disable=invalid-name
    """
    dirty helper
    """
    if type(value) is unicode:
        return value.encode(STDOUT_ENCODING)
    else:
        return value

# Use the built-in version of scandir/walk if possible, otherwise
# use the scandir module version

USING_SCANDIR = True

try:
    if sys.version_info < (3, 5):
        from scandir import walk
        os.walk = walk
except ImportError:
    USING_SCANDIR = False

from multiprocessing.managers import SyncManager

G_MESSAGES_QUEUE = None

# Copy and paste from bup/options.py
# I'm disabling some pylint warning here

# pylint: disable=bad-whitespace, bad-continuation, unused-variable, invalid-name, wrong-import-position
# pylint: disable=reimported, missing-docstring, too-few-public-methods, unused-argument
# pylint: disable=too-many-instance-attributes, old-style-class, too-many-locals, multiple-statements
# pylint: disable=protected-access, superfluous-parens, pointless-string-statement, too-many-branches
# pylint: disable=too-many-statements, broad-except

# Copyright 2010-2012 Avery Pennarun and options.py contributors.
# All rights reserved.
#
# (This license applies to this file but not necessarily the other files in
# this package.)
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#    1. Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in
#       the documentation and/or other materials provided with the
#       distribution.
#
# THIS SOFTWARE IS PROVIDED BY AVERY PENNARUN ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
"""Command-line options parser.
With the help of an options spec string, easily parse command-line options.

An options spec is made up of two parts, separated by a line with two dashes.
The first part is the synopsis of the command and the second one specifies
options, one per line.

Each non-empty line in the synopsis gives a set of options that can be used
together.

Option flags must be at the begining of the line and multiple flags are
separated by commas. Usually, options have a short, one character flag, and a
longer one, but the short one can be omitted.

Long option flags are used as the option's key for the OptDict produced when
parsing options.

When the flag definition is ended with an equal sign, the option takes
one string as an argument, and that string will be converted to an
integer when possible. Otherwise, the option does not take an argument
and corresponds to a boolean flag that is true when the option is
given on the command line.

The option's description is found at the right of its flags definition, after
one or more spaces. The description ends at the end of the line. If the
description contains text enclosed in square brackets, the enclosed text will
be used as the option's default value.

Options can be put in different groups. Options in the same group must be on
consecutive lines. Groups are formed by inserting a line that begins with a
space. The text on that line will be output after an empty line.
"""
import sys, os, textwrap, getopt, re, struct


def _invert(v, invert): # pragma: no cover
    if invert:
        return not v
    return v


def _remove_negative_kv(k, v): # pragma: no cover
    if k.startswith('no-') or k.startswith('no_'):
        return k[3:], not v
    return k,v


class OptDict(object): # pragma: no cover
    """Dictionary that exposes keys as attributes.

    Keys can be set or accessed with a "no-" or "no_" prefix to negate the
    value.
    """
    def __init__(self, aliases):
        self._opts = {}
        self._aliases = aliases

    def _unalias(self, k):
        k, reinvert = _remove_negative_kv(k, False)
        k, invert = self._aliases[k]
        return k, invert ^ reinvert

    def __setitem__(self, k, v):
        k, invert = self._unalias(k)
        self._opts[k] = _invert(v, invert)

    def __getitem__(self, k):
        k, invert = self._unalias(k)
        return _invert(self._opts[k], invert)

    def __getattr__(self, k):
        return self[k]


def _default_onabort(msg): # pragma: no cover
    sys.exit(97)


def _intify(v): # pragma: no cover
    try:
        vv = int(v or '')
        if str(vv) == v:
            return vv
    except ValueError:
        pass
    return v


def _atoi(v): # pragma: no cover
    try:
        return int(v or 0)
    except ValueError:
        return 0


def _tty_width(): # pragma: no cover
    # modification from the msrsync project : if sys.stderr is xStringIO or something else...
    if not hasattr(sys.stderr, "fileno"):
        return _atoi(os.environ.get('WIDTH')) or 70
    s = struct.pack("HHHH", 0, 0, 0, 0)
    try:
        import fcntl, termios
        s = fcntl.ioctl(sys.stderr.fileno(), termios.TIOCGWINSZ, s)
    except (IOError, ImportError):
        return _atoi(os.environ.get('WIDTH')) or 70
    (ysize,xsize,ypix,xpix) = struct.unpack('HHHH', s)
    return xsize or 70


class Options: # pragma: no cover
    """Option parser.
    When constructed, a string called an option spec must be given. It
    specifies the synopsis and option flags and their description.  For more
    information about option specs, see the docstring at the top of this file.

    Two optional arguments specify an alternative parsing function and an
    alternative behaviour on abort (after having output the usage string).

    By default, the parser function is getopt.gnu_getopt, and the abort
    behaviour is to exit the program.
    """
    def __init__(self, optspec, optfunc=getopt.gnu_getopt,
                 onabort=_default_onabort):
        self.optspec = optspec
        self._onabort = onabort
        self.optfunc = optfunc
        self._aliases = {}
        self._shortopts = 'h?'
        self._longopts = ['help', 'usage']
        self._hasparms = {}
        self._defaults = {}
        self._usagestr = self._gen_usage()  # this also parses the optspec

    def _gen_usage(self):
        out = []
        lines = self.optspec.strip().split('\n')
        lines.reverse()
        first_syn = True
        while lines:
            l = lines.pop()
            if l == '--': break
            out.append('%s: %s\n' % (first_syn and 'usage' or '   or', l))
            first_syn = False
        out.append('\n')
        last_was_option = False
        while lines:
            l = lines.pop()
            if l.startswith(' '):
                out.append('%s%s\n' % (last_was_option and '\n' or '',
                                       l.lstrip()))
                last_was_option = False
            elif l:
                (flags,extra) = (l + ' ').split(' ', 1)
                extra = extra.strip()
                if flags.endswith('='):
                    flags = flags[:-1]
                    has_parm = 1
                else:
                    has_parm = 0
                g = re.search(r'\[([^\]]*)\]$', extra)
                if g:
                    defval = _intify(g.group(1))
                else:
                    defval = None
                flagl = flags.split(',')
                flagl_nice = []
                flag_main, invert_main = _remove_negative_kv(flagl[0], False)
                self._defaults[flag_main] = _invert(defval, invert_main)
                for _f in flagl:
                    f,invert = _remove_negative_kv(_f, 0)
                    self._aliases[f] = (flag_main, invert_main ^ invert)
                    self._hasparms[f] = has_parm
                    if f == '#':
                        self._shortopts += '0123456789'
                        flagl_nice.append('-#')
                    elif len(f) == 1:
                        self._shortopts += f + (has_parm and ':' or '')
                        flagl_nice.append('-' + f)
                    else:
                        f_nice = re.sub(r'\W', '_', f)
                        self._aliases[f_nice] = (flag_main,
                                                 invert_main ^ invert)
                        self._longopts.append(f + (has_parm and '=' or ''))
                        self._longopts.append('no-' + f)
                        flagl_nice.append('--' + _f)
                flags_nice = ', '.join(flagl_nice)
                if has_parm:
                    flags_nice += ' ...'
                prefix = '    %-20s  ' % flags_nice
                argtext = '\n'.join(textwrap.wrap(extra, width=_tty_width(),
                                                initial_indent=prefix,
                                                subsequent_indent=' '*28))
                out.append(argtext + '\n')
                last_was_option = True
            else:
                out.append('\n')
                last_was_option = False
        return ''.join(out).rstrip() + '\n'

    def usage(self, msg=""):
        """Print usage string to stderr and abort."""
        sys.stderr.write(self._usagestr)
        if msg:
            sys.stderr.write(msg)
        e = self._onabort and self._onabort(msg) or None
        if e:
            raise e

    def fatal(self, msg):
        """Print an error message to stderr and abort with usage string."""
        msg = '\nerror: %s\n' % msg
        return self.usage(msg)

    def parse(self, args):
        """Parse a list of arguments and return (options, flags, extra).

        In the returned tuple, "options" is an OptDict with known options,
        "flags" is a list of option flags that were used on the command-line,
        and "extra" is a list of positional arguments.
        """
        try:
            (flags,extra) = self.optfunc(args, self._shortopts, self._longopts)
        except getopt.GetoptError, e:
            self.fatal(e)

        opt = OptDict(aliases=self._aliases)

        for k,v in self._defaults.iteritems():
            opt[k] = v

        for (k,v) in flags:
            k = k.lstrip('-')
            if k in ('h', '?', 'help', 'usage'):
                self.usage()
            if (self._aliases.get('#') and
                  k in ('0','1','2','3','4','5','6','7','8','9')):
                v = int(k)  # guaranteed to be exactly one digit
                k, invert = self._aliases['#']
                opt['#'] = v
            else:
                k, invert = opt._unalias(k)
                if not self._hasparms[k]:
                    assert(v == '')
                    v = (opt._opts.get(k) or 0) + 1
                else:
                    v = _intify(v)
            opt[k] = _invert(v, invert)
        return (opt,flags,extra)

# pylint: enable=bad-whitespace, bad-continuation, unused-variable, invalid-name
# pylint: enable=reimported, missing-docstring, too-few-public-methods, unused-argument
# pylint: enable=too-many-instance-attributes, old-style-class, too-many-locals, multiple-statements
# pylint: enable=protected-access, superfluous-parens, pointless-string-statement, too-many-branches
# pylint: enable=too-many-statements, broad-except

def print_message(message, output=MSG_STDOUT):
    """
    Add message to the message queue
    """
    G_MESSAGES_QUEUE.put({"type": output, "message": message})


def print_update(data):
    """
    Print 'data' on the same line as before
    """
    sys.stdout.write("\r\x1b[K"+data.__str__())
    sys.stdout.flush()


class BucketError(RuntimeError):
    """
    Exception for bucket related error
    """
    pass


def _check_python_version():
    """
    Stupid python version checker
    """
    major, minor, _ = platform.python_version_tuple()
    if major == 2 and minor < 6:
        python26_release = datetime.datetime(2008, 10, 1)
        now = datetime.datetime.now()
        years = (now - python26_release).days / 365
        sys.stderr.write(("You need python >= 2.6 to run this program (more than %d years old)." + os.linesep) % years)
        sys.exit(EPYTHON_VERSION)


def get_human_size(num, power="B"):
    """
    Stolen from the ps_mem.py project for nice size output :)
    """
    powers = ["B", "K", "M", "G", "T", "P", "E", "Z", "Y"]
    while num >= 1000: #4 digits
        num /= 1024.0
        power = powers[powers.index(power)+1]
    return "%.1f %s" % (num, power)


def human_size(value):
    """
    parse the provided human size (with multiples K, M, G, T, E, P, Z, Y)
    and return bytes
    """

    if value.isdigit():
        return int(value)

    if not value[:-1].isdigit():
        return None

    m2s = {'K': 1024, \
           'M': 1024*1024, \
           'G': 1024*1024*1024, \
           'T': 1024*1024*1024*1024, \
           'P': 1024*1024*1024*1024*1024, \
           'E': 1024*1024*1024*1024*1024*1024, \
           'Z': 1024*1024*1024*1024*1024*1024*1024, \
           'Y': 1024*1024*1024*1024*1024*1024*1024*1024}

    size = int(value[:-1])
    multiple = value[-1]

    if multiple not in m2s.keys():
        return None

    return size * m2s[multiple]


def crawl(path, relative=False):
    """
    Simple generator around os.walk that will
    yield (size, fullpath) tuple for each file or link
    underneath path.

    If relative is True, the path will be relative to path, without
    any leading ./ or /. For exemple, crawl("/home/jbdenis/Code", relative=True)
    will yield (42, "toto") for "/home/jbdenis/Code/toto" file
    """
    def onerror(oserror):
        """
        helper
        """
        print_message("msrsync crawl: %s" % oserror, MSG_STDERR)

    root_size = len(path) if relative else 0

    for root, dirs, files in os.walk(path, onerror=onerror):
        # we want empty dir to be listed in bucket
        if len(dirs) == 0 and len(files) == 0:
            rpath = root[root_size:]
            try:
                yield os.lstat(root).st_size, rpath
            except OSError, err:
                print_message("msrsync crawl: %s" % err, MSG_STDERR)
                continue

        dir_links = [d for d in dirs if os.path.islink(os.path.join(root, d))]

        for name in itertools.chain(files, dir_links):
            fullpath = os.path.join(root, name)
            try:
                size = os.lstat(fullpath).st_size
            except OSError, err:
                print_message("msrsync crawl: %s" % err, MSG_STDERR)
                continue

            rpath = fullpath[root_size:]
            yield size, rpath


def buckets(path, filesnr, size):
    """
    Split files underneath path in buckets less than <size> bytes in total
    or containing <filesnr> files maximum.
    """
    bucket_files_nr = 0
    bucket_size = 0
    bucket = list()


    # if we've got a trailing slash in the path, we want
    # to sync the content of the path. I
    # if we don't have a trailing slash, we want to sync the path
    # itself
    # Example:
    # os.path.split("/home/jbdenis/Code")[1] will return 'Code'
    # os.path.split("/home/jbdenis/Code/")[1] will return ''
    base = os.path.split(path)[1]

    for fsize, rpath in crawl(path, relative=True):
        bucket.append(os.path.join(base, rpath.lstrip(os.sep)))
        bucket_files_nr += 1
        bucket_size += fsize

        if bucket_size >= size or bucket_files_nr >= filesnr:
            yield (bucket_files_nr, bucket_size, bucket)
            bucket_size = 0
            bucket_files_nr = 0
            bucket = list()

    if bucket_files_nr > 0:
        yield (bucket_files_nr, bucket_size, bucket)


def _valid_rsync_options(options, rsync_opts):
    """
    Check for weird stuff in rsync options
    """
    rsync_args = rsync_opts.split()
    for opt in rsync_args:
        if opt.startswith("--delete"):
            options.fatal("Cannot use --delete option type with msrsync. It would lead to disaster :)")


def parse_cmdline(cmdline_argv):
    """
    command line parsing of msrsync using bup/options.py
    See https://github.com/bup/bup/blob/master/lib/bup/options.py
    """

    # If I want to run this script on RHEL6 and derivatives without installing any dependencies
    # except python and rsync, I can't rely on argparse which is only available in python >= 2.7
    # standard library. I don't want to rely on the installation of python-argparse for python 2.6
    options = Options(MSRSYNC_OPTSPEC)

    # it looks soooo fragile, but it works for me here.
    # this block extracts the provided rsync options if present
    # it assumes thats the SRC... DEST arguments are at the end of the command line
    # I cannot use options parser to parse --rsync since some msrsync use some options
    # name already used by rsync. So I only parse the command line up to the --rsync token
    # and ugly parse what I want. Any better idea ?
    if "-r" in cmdline_argv:
        idx = cmdline_argv.index("-r")
        cmdline_argv[idx] = "--rsync"

    if "--rsync" in cmdline_argv:
        idx = cmdline_argv.index("--rsync")
        # we parse the command line up to --rsync options marker
        (opt, _, extra) = options.parse(cmdline_argv[1:idx])
        if len(cmdline_argv[idx:]) < 4: # we should have, at least, something like --rsync "-avz --whatever" src dest
            options.fatal('You must provide a source, a destination and eventually rsync options with --rsync')
        opt.rsync = opt.r = cmdline_argv[idx+1] # pylint: disable=invalid-name, attribute-defined-outside-init
        _valid_rsync_options(options, opt.rsync)
        srcs, dest = cmdline_argv[idx+2:-1], cmdline_argv[-1]
    else:
        # no --rsync options marker on the command line.
        (opt, _, extra) = options.parse(cmdline_argv[1:])
        if opt.selftest or opt.bench or opt.benchshm or opt.version: # early exit
            return opt, [], ""
        opt.rsync = opt.r = DEFAULT_RSYNC_OPTIONS # pylint: disable=attribute-defined-outside-init
        if not extra or len(extra) < 2:
            options.fatal('You must provide a source and a destination')
        srcs, dest = extra[:-1], extra[-1]

    size = human_size(str(opt.size))
    if not size:
        options.fatal("'%s' does not look like a valid size value" % opt.size)
    try:
        # pylint: disable=attribute-defined-outside-init, invalid-name
        opt.files = opt.f = int(opt.f)
    except ValueError:
        options.fatal("'%s' does not look like a valid files number value" % opt.f)
    opt.size = opt.s = size # pylint: disable=invalid-name, attribute-defined-outside-init
    opt.compress = False # pylint: disable=attribute-defined-outside-init

    return opt, srcs, dest


def rmtree_onerror(func, path, exc_info):
    """
    Error handler for shutil.rmtree.
    """
    # pylint: disable=unused-argument
    print >>sys.stderr, "Error removing", path


def write_bucket(filename, bucket, compress=False):
    """
    Dump bucket filenames in a optionnaly compressed file
    """
    try:
        fileno, path = filename
        if not compress:
            with os.fdopen(fileno, 'wb') as bfile:
                for entry in bucket:
                    bfile.write(entry + '\0')
        else:
            os.close(fileno)
            with gzip.open(path, 'wb') as bfile:
                for entry in bucket:
                    bfile.write(entry)
    except IOError, err:
        raise BucketError("Cannot write bucket file %s: %s" % (path, err))


def consume_queue(jobs_queue):
    """
    Simple helper around a shared queue
    """
    while True:
        item = jobs_queue.get()
        if item is StopIteration:
            return
        yield item


# stolen from Forest http://stackoverflow.com/questions/1191374/subprocess-with-timeout
def kill_proc(proc, timeout):
    """ helper function for run """
    timeout["value"] = True
    try:
        proc.kill()
    except OSError:
        pass


# stolen and adapted from Forest http://stackoverflow.com/questions/1191374/subprocess-with-timeout
def run(cmd, capture_stdout=False, capture_stderr=False, timeout_sec=sys.maxsize):
    """ run function with a timeout """
    try:
        stdout_p = subprocess.PIPE if capture_stdout else None
        stderr_p = subprocess.PIPE if capture_stderr else None
        proc = subprocess.Popen(shlex.split(cmd), stdout=stdout_p, stderr=stderr_p)
        timeout = {"value": False}
        timer = threading.Timer(timeout_sec, kill_proc, [proc, timeout])
        starttime = time.time()
        timer.start()
        stdout, stderr = proc.communicate()
        if stdout is None:
            stdout = ""
        if stderr is None:
            stderr = ""
        timer.cancel()
        elapsed = time.time() - starttime
    except OSError, err:
        return -1, "", "Cannot launch %s: %s" % (cmd, err), False, 0
    except KeyboardInterrupt:
        if vars().has_key('timer'):
            timer.cancel()
        if proc:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            proc.terminate()
            proc.wait()
        return 666, "", "Interrupted", False, 0

    return proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"), timeout["value"], elapsed


# stolen from stackoverflow (http://stackoverflow.com/a/377028)
def which(program):
    """
    Python implementation of the which command
    """
    def is_exe(fpath):
        """ helper """
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, _ = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None


def _check_rsync_options(options):
    """
    Build a command line given the rsync options string
    and try to execute it on empty directory
    """
    rsync_cmd = None
    try:
        src = tempfile.mkdtemp()
        dst = tempfile.mkdtemp()
        rsync_log_fd, rsync_log = tempfile.mkstemp()
        rsync_cmd = "%s %s %s %s" % (RSYNC_EXE, options + ' --quiet --stats --verbose --from0 --log-file %s' % rsync_log, src + os.sep, dst)
        ret, _, stderr, timeout, _ = run(rsync_cmd, timeout_sec=60) # this should not take more than one minute =)
        if timeout:
            print >>sys.stderr, '''Error during rsync options check command "%s": took more than 60 seconds !''' % rsync_cmd
            sys.exit(ERSYNC_OPTIONS_CHECK)
        elif ret != 0:
            print >>sys.stderr, '''Error during rsync options check command "%s": %s''' % (rsync_cmd, 2*os.linesep + stderr)
            sys.exit(ERSYNC_OPTIONS_CHECK)
    except OSError, err:
        if rsync_cmd:
            print >>sys.stderr, '''Error during rsync options check command "%s": %s''' % (rsync_cmd, 2*os.linesep + err)
        else:
            print >>sys.stderr, '''Error during rsync options check ("%s"): %s''' % (options, 2*os.linesep + err)
        sys.exit(ERSYNC_OPTIONS_CHECK)
    finally:
        try:
            os.rmdir(src)
            os.rmdir(dst)
            os.close(rsync_log_fd)
            os.remove(rsync_log)
        except OSError:
            pass


def run_rsync(files_from, rsync_opts, src, dest, timeout=3600*24*7):
    """
    Perform rsync using the --files-from option
    """
    # this looks very close to the _check_rsync_options function...
    # except the error message
    rsync_log = files_from + '.log'
    rsync_cmd = '%s %s %s "%s" "%s"' % (RSYNC_EXE, rsync_opts, "--quiet --verbose --stats --from0 --files-from=%s --log-file %s" % (files_from, rsync_log), src, dest)
    #rsync_cmd = "%s %s %s %s %s" % (RSYNC_EXE, rsync_opts, "--quiet --from0 --files-from=%s" % (files_from,), src, dest)

    rsync_result = dict()
    rsync_result["rcode"] = -1
    rsync_result["msg"] = None
    rsync_result["cmdline"] = rsync_cmd
    rsync_result["log"] = rsync_log

    try:
        ret, _, _, timeout, elapsed = run(rsync_cmd, timeout_sec=timeout)

        rsync_result["rcode"] = ret
        rsync_result["elapsed"] = elapsed

        if timeout:
            rsync_result["errcode"] = ERSYNC_TOO_LONG
        elif ret != 0:
            rsync_result["errcode"] = ERSYNC_JOB

    except OSError, err:
        rsync_result["errcode"] = ERSYNC_JOB
        rsync_result["msg"] = str(err)

    return rsync_result


def rsync_worker(jobs_queue, monitor_queue, options, dest):
    """
    The queue will contains filenames of file to handle by individual rsync processes
    """

    try:
        for src, files_from, bucket_files_nr, bucket_size in consume_queue(jobs_queue):
            if not options.dry_run:
                rsync_result = run_rsync(files_from, options.rsync, src, dest)
            else:
                rsync_result = dict(rcode=0, elapsed=0, errcode=0, msg='')
            rsync_mon_result = {"type": TYPE_RSYNC, "rsync_result": rsync_result, "size": bucket_size, "files_nr": bucket_files_nr, "jq_size": jobs_queue.qsize()}
            monitor_queue.put(rsync_mon_result)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        jobs_queue.put(StopIteration)
        # we insert a sentinel value to inform the monitor this process fnished
        monitor_queue.put({"type": TYPE_RSYNC_SENTINEL, "pid": os.getpid()})


def handle_rsync_error_result(rsync_result):
    """"
    Helper
    """
    msg = ''

    if rsync_result["msg"] is not None:
        msg = rsync_result["msg"]

    if rsync_result["errcode"] == ERSYNC_TOO_LONG:
        print_message(("rsync command took too long and has been killed (see '%s' rsync log file): %s\n" + rsync_result["cmdline"]) % (rsync_result["log"], msg), MSG_STDERR)
    elif rsync_result["errcode"] == ERSYNC_JOB:
        print_message(("errors during rsync command (see '%s' rsync log file): %s\n" + rsync_result["cmdline"]) % (rsync_result["log"], msg), MSG_STDERR)
    else:
        print_message("unknown rsync_result status: %s" % rsync_result, MSG_STDERR)


def rsync_monitor_worker(monitor_queue, nb_rsync_processes, total_size, total_files_nr, crawl_time, total_time, options):
    """
    The monitor queue contains messages from the rsync workers
    """
    current_size = 0
    current_files_nr = 0
    current_elapsed = 0
    rsync_runtime = 0
    rsync_workers_stops = 0
    buckets_nr = 0
    rsync_errors = 0
    entries_per_second = 0
    bytes_per_second = 0

    try:
        start = timeit.default_timer()
        for result in consume_queue(monitor_queue):
            if result["type"] == TYPE_RSYNC_SENTINEL:
                # not needed, but we keep it for now
                rsync_workers_stops += 1
                continue
            if result["type"] != TYPE_RSYNC:
                print_message("rsync_monitor_worker process received an incompatile type message: %s" % result, MSG_STDERR)
                continue

            rsync_result = result["rsync_result"]

            if rsync_result["rcode"] != 0:
                rsync_errors += 1
                handle_rsync_error_result(rsync_result)
                continue

            buckets_nr += 1
            current_size += result["size"]
            current_files_nr += result["files_nr"]
            rsync_runtime += result["rsync_result"]["elapsed"]
            current_elapsed = timeit.default_timer() - start

            if current_elapsed > 0:
                bytes_per_second = current_size / current_elapsed
            else:
                bytes_per_second = 0

            if current_elapsed > 0:
                entries_per_second = current_files_nr / current_elapsed
            else:
                entries_per_second = 0

            if options.progress:
                print_message("[%d/%d entries] [%s/%s transferred] [%d entries/s] [%s/s bw] [monq %d] [jq %d]" % \
                              (current_files_nr,\
                               total_files_nr.value,\
                               get_human_size(current_size),\
                               get_human_size(total_size.value),\
                               entries_per_second,\
                               get_human_size(bytes_per_second),\
                               monitor_queue.qsize(),\
                               result["jq_size"]),\
                               MSG_PROGRESS)


        if rsync_errors > 0:
            print_message("\nmsrsync error: somes files/attr were not transferred (see previous errors)", MSG_STDERR)

        stats = dict()
        stats["errors"] = rsync_errors
        stats["total_size"] = total_size.value
        stats["total_entries"] = total_files_nr.value
        stats["buckets_nr"] = buckets_nr
        stats["bytes_per_second"] = bytes_per_second
        stats["entries_per_second"] = entries_per_second
        stats["rsync_workers"] = nb_rsync_processes
        stats["rsync_runtime"] = rsync_runtime
        stats["crawl_time"] = crawl_time.value
        stats["total_time"] = total_time.value

        monitor_queue.put(stats)

    except (KeyboardInterrupt, SystemExit):
        pass


def messages_worker(options):
    """
    This queue will contains messages to be print of the screen
    """

    last_msg_type = cur_msg_type = None
    try:
        for result in consume_queue(G_MESSAGES_QUEUE):
            if last_msg_type == MSG_PROGRESS:
                newline = os.linesep
            else:
                newline = ''

            cur_msg_type = result["type"]

            if cur_msg_type == MSG_PROGRESS:
                print_update(result["message"])
            elif cur_msg_type == MSG_STDOUT:
                print >>sys.stdout, _e(newline + result["message"])
            elif cur_msg_type == MSG_STDERR:
                print >>sys.stderr, _e(newline + result["message"])
            else:
                print >>sys.stderr, _e(newline + "Unknown message type '%s': %s" % (cur_msg_type, result))
            last_msg_type = cur_msg_type

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if last_msg_type == MSG_PROGRESS:
            print >>sys.stdout, ''

def start_rsync_workers(jobs_queue, monitor_queue, options, dest):
    """
    Helper to start rsync processes
    """
    processes = []
    for _ in xrange(options.processes):
        processes.append(multiprocessing.Process(target=rsync_worker, args=(jobs_queue, monitor_queue, options, dest)))
        processes[-1].start()
    return processes


def start_rsync_monitor_worker(monitor_queue, nb_rsync_processes, total_size, total_files_nr, crawl_time, total_time, options):
    """
    Helper to start rsync monitor process
    """
    proc = multiprocessing.Process(target=rsync_monitor_worker, args=(monitor_queue, nb_rsync_processes, total_size, total_files_nr, crawl_time, total_time, options))
    proc.start()
    return proc


def start_messages_worker(options):
    """
    Helper to start messages process
    """
    proc = multiprocessing.Process(target=messages_worker, args=(options,))
    proc.start()
    return proc


def multiprocess_mgr_init():
    """
    Explicit initializer for SyncManager in msrsync function
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def show_stats(msrsync_stat):
    """
    Show the stats from msrsync run
    """
    s = msrsync_stat
    if s["errors"] == 0:
        status = "SUCCESS"
    else:
        status = "FAILURE, %d rsync processe(s) had errors" % s["errors"]

    print "Status:", status
    print "Working directory:", os.getcwd()
    print "Command line:", " ".join(sys.argv)
    print "Total size: %s" % get_human_size(s["total_size"])
    print "Total entries: %s" % s["total_entries"]
    buckets_nr = s["buckets_nr"]
    print "Buckets number: %d" % buckets_nr
    if buckets_nr > 0:
        print "Mean entries per bucket: %d" % ((s["total_entries"] * 1.)/ buckets_nr)
        print "Mean size per bucket: %s" % get_human_size((s["total_size"] * 1.)/ buckets_nr)

    print "Entries per second: %d" % s["entries_per_second"]
    print "Speed: %s/s" % get_human_size(s["bytes_per_second"])
    print "Rsync workers: %d" % s["rsync_workers"]
    print "Total rsync's processes (%d) cumulative runtime: %.1fs" % (buckets_nr, s["rsync_runtime"])
    print "Crawl time: %.1fs (%.1f%% of total runtime)" % (s["crawl_time"], 100* s["crawl_time"]/s["total_time"])
    print "Total time: %.1fs" % s["total_time"]


def msrsync(options, srcs, dest):
    """
    multi-stream rsync reusable function
    It will copy srcs directories to dest honoring the options structure
    """
    global G_MESSAGES_QUEUE
    # pylint: disable=too-many-branches, too-many-locals
    try:
        if not options.buckets:
            options.buckets = tempfile.mkdtemp(prefix="msrsync-")
        else:
            if not os.path.exists(options.buckets):
                print >>sys.stderr, options.buckets, "bucket directory does not exist."
                sys.exit(EBUCKET_DIR_NOEXIST)
            if not os.access(options.buckets, os.W_OK):
                print >>sys.stderr, options.buckets, "bucket directory is not writable."
                sys.exit(EBUCKET_DIR_PERMS)
            options.buckets = tempfile.mkdtemp(prefix="msrsync-", dir=options.buckets)
    except OSError, err:
        print >>sys.stderr, '''Error with bucket directory creation: "%s"''' % err
        sys.exit(EBUCKET_DIR_OSERROR)

    if options.show:
        print "buckets dir is", options.buckets

    manager = SyncManager()
    #manager.start(multiprocess_mgr_init) # Oups... This is in python 2.7...
    manager.start()

    total_size = manager.Value('i', 0)
    total_files_nr = manager.Value('i', 0)
    crawl_time = manager.Value('f', 0)
    total_time = manager.Value('f', 0)

    monitor_queue = manager.Queue() # pylint: disable=no-member
    jobs_queue = manager.Queue() # pylint: disable=no-member
    G_MESSAGES_QUEUE = manager.Queue() # pylint: disable=no-member

    rsync_workers_procs = start_rsync_workers(jobs_queue, monitor_queue, options, dest)
    rsync_monitor_worker_proc = start_rsync_monitor_worker(monitor_queue, options.processes, total_size, total_files_nr, crawl_time, total_time, options)
    messages_worker_proc = start_messages_worker(options)

    crawl_start = timeit.default_timer()

    try:
        total_size.value = 0
        bucket_nr = 0
        for src in srcs:
            # do we want to sync to content of src or src itself ?
            # os.path.split("/home/jbdenis/Code")[0] will return '/home/jbdenis'
            # os.path.split("/home/jbdenis/Code/")[0] will return '/home/jbdenis/Code'
            # We need that to correctly generate the --files-from files and the src path
            # that will be used on the rsync command line

            # if src is a single directory, without trailing slash
            # os.path.split("src") returns ('', 'src')
            head, tail = os.path.split(src)
            if head == '':
                src_base = os.getcwd()
            else:
                src_base = head

            for bucket_files_nr, bucket_size, bucket in buckets(src, options.files, options.s):
                total_size.value += bucket_size
                total_files_nr.value += bucket_files_nr
                # from the rsync man page (--files-from part):
                # NOTE: sorting the list of files in the --files-from input
                # helps rsync to be more efficient, as it will avoid re-visiting
                # the path elements that are shared  between  adjacent
                # entries.  If the input is not sorted, some path elements
                # (implied directories) may end up being scanned multiple times,
                # and rsync will eventually unduplicate them after they
                # get turned into file-list elements.
                bucket.sort()
                # the idea is prevent to have too much files in a single directory
                d1s = str(bucket_nr / 1024).zfill(8)
                # with bucket_nr == 12, d1s == i'00000000'
                # tdir = options.buckets/0000/0000
                # with bucket_nr == 11058, d1s == '00000100'
                # tdir = options.buckets/0000/0012
                # with bucket_nr == 148472185, d1s == '00144992' (148472181/1024)
                # tdir = options.buckets/0014/4992
                try:
                    tdir = os.path.join(options.buckets, d1s[:4], d1s[4:])
                    if not os.path.exists(tdir):
                        os.makedirs(tdir)
                    # the fd is closed within write_bucket
                    fileno, filename = tempfile.mkstemp(dir=tdir)
                except OSError, err:
                    print_message('msrsync scan: cannot create temporary bucket file: "%s"' % err, MSG_STDERR)
                    continue
                write_bucket((fileno, filename), bucket, options.compress)
                bucket_nr += 1
                jobs_queue.put((src_base, filename, bucket_files_nr, bucket_size))

        crawl_time.value = timeit.default_timer() - crawl_start

        jobs_queue.put(StopIteration)

        for worker in rsync_workers_procs:
            worker.join()

        total_time.value = timeit.default_timer() - crawl_start

        monitor_queue.put(StopIteration)
        rsync_monitor_worker_proc.join()

        G_MESSAGES_QUEUE.put(StopIteration)
        messages_worker_proc.join()

        # we retrieve the last element from the queue, which is a stats dict
        run_stats = monitor_queue.get()
        if options.stats:
            show_stats(run_stats)

        return run_stats["errors"]

    except (KeyboardInterrupt, SystemExit):
        for worker in rsync_workers_procs:
            worker.terminate()
            worker.join()

        rsync_monitor_worker_proc.terminate()
        rsync_monitor_worker_proc.join()

        messages_worker_proc.terminate()
        messages_worker_proc.join()
    except BucketError, err:
        print >>sys.stderr, err
    except Exception: # pylint: disable=broad-except
        print >>sys.stderr, "Uncaught exception:" + os.linesep + traceback.format_exc()
    finally:
        manager.shutdown()
        if options.buckets is not None and not options.keep:
            shutil.rmtree(options.buckets, onerror=rmtree_onerror)


def _check_executables():
    """
    Get the full path of somes binaries
    """
    global RSYNC_EXE # pylint: disable=global-statement

    exes = ["rsync"]
    paths = dict()

    for exe in exes:
        prog = which(exe)
        if not prog:
            print >>sys.stderr, "Cannot find '%s' executable in PATH." % exe
            sys.exit(EBIN_NOTFOUND)
        paths[exe] = prog

    RSYNC_EXE = paths["rsync"]


def _check_srcs_dest(srcs, dest):
    """
    Check that the supplied arguments are valid
    """
    for src in srcs:
        if not os.path.isdir(src):
            print >>sys.stderr, "Source '%s' is not a directory" % src
            sys.exit(ESRC_NOT_DIR)
        if not os.access(src, os.R_OK|os.X_OK):
            print >>sys.stderr, "No access to source directory '%s'" % src
            sys.exit(ESRC_NO_ACCESS)

    # dest may not exist, just as in rsync : "rsync -a src dst" will create
    # destination if it does not exist. But I prefer to create it here to handle
    # potential errors
    if not os.path.exists(dest):
        try:
            os.mkdir(dest)
        except OSError, err:
            print >>sys.stderr, "Error creating destination directory '%s': %s" % (dest, err)
            sys.exit(EDEST_CREATE)

    if os.path.isfile(dest):
        print >>sys.stderr, "Destination '%s' already exists and is a file" % dest
        sys.exit(EDEST_IS_FILE)

    if os.path.isdir(dest) and not os.access(dest, os.W_OK|os.X_OK):
        print >>sys.stderr, "Destination directory '%s' not writable" % dest
        sys.exit(EDEST_NO_ACCESS)


def _create_level_entries(cwd, max_entries, files_pct):
    """
    Helper for testing purpose

    It will create "max_entries" entries in "cwd" with
    files_pct percent of files. The rest will be directories
    """
    dirs = list()
    files_nr = 0
    level_entries = random.randint(0, max_entries)

    for _ in xrange(level_entries):
        if random.randint(1, 100) <= files_pct:
            fhandle, _ = tempfile.mkstemp(dir=cwd)
            os.close(fhandle)
            files_nr += 1
        else:
            dirname = tempfile.mkdtemp(dir=cwd)
            dirs.append(dirname)

    return files_nr + len(dirs), dirs


def _create_fake_tree(cwd, total_entries, max_entries_per_level, max_depth, files_pct):
    """
    Helper for testing purpose

    This function will create a tree of 'total_entries' files and dirs in cwd, trying
    not to put more that "max_entries_per_level" entries at each level and if possible
    not to exceed a "max_depth" depth.

    The ratio of files/dirs is controlled with "files_pct". For example, if files_pct is "90",
    this function will create a tree with 90% of files and 10% of directories
    """
    dir_queue = list()
    dir_queue.append(cwd)
    curr_entries_number = 0

    root_len = len(cwd)

    while curr_entries_number < total_entries:
        if len(dir_queue) == 0:
            dir_queue.append(cwd)

        cur = dir_queue.pop()

        if cur[root_len:].count(os.sep) >= max_depth:
            continue

        entries_to_create = total_entries - curr_entries_number
        if entries_to_create < max_entries_per_level:
            max_entries = entries_to_create
        else:
            max_entries = max_entries_per_level

        entries, dirs = _create_level_entries(cur, max_entries, files_pct)
        curr_entries_number += entries
        dir_queue.extend(dirs)

    return curr_entries_number


def _compare_trees(first, second):
    """
    Helper for testing purpose

    This function takes two paths, generate a listing.
    and compare them. The goal is to determine if two trees are "equal". See note.

    Note: since os.walk herits the behaviour of os.listdir, we can have different
    walk listing order for the same tree. We need to sort it before comparing anything.
    It does not scale. Ideally, the file listings would have been written in temporaries
    files and then merge-sorted (like sort(1)). Use only on tree with reasonnable size
    """
    first_list = second_list = list()
    for _, cur in crawl(first, relative=True):
        first_list.append(cur)

    first_list.sort()

    for _, cur in crawl(second, relative=True):
        first_list.append(cur)

    second_list.sort()

    return first_list == second_list


class TestHelpers(unittest.TestCase):
    """
    Test the various function helpers
    """
    # pylint: disable=too-many-public-methods
    def test_get_human_size(self):
        """ convert bytes to human readable string """
        val = get_human_size(1024)
        self.assertEquals(val, '1.0 K')

    def test_get_human_size2(self):
        """ convert bytes to human readable string """
        val = get_human_size(1024*1024)
        self.assertEquals(val, '1.0 M')

    def test_human_size(self):
        """ convert human readable size to bytes """
        val = human_size("1024")
        self.assertEquals(val, 1024)

    def test_human_size2(self):
        """ convert human readable size to bytes """
        val = human_size("1M")
        self.assertEquals(val, 1024*1024)

    def test_human_size3(self):
        """ wrongly formatted size """
        val = human_size("10KK")
        self.assertEquals(val, None)

    def test_human_size4(self):
        """ bad suffix  """
        val = human_size("10Q")
        self.assertEquals(val, None)


class TestOptionsParser(unittest.TestCase):
    """
    Test the command line parsing
    """
    # pylint: disable=too-many-public-methods
    def test_nooption(self):
        """ parse cmdline without argument"""
        try:
            cmdline = shlex.split("msrsync")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")

    def test_justrsync(self):
        """ parse cmdline with only --rsync option"""
        try:
            cmdline = shlex.split("msrsync --rsync")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")

    def test_badsize(self):
        """ parse cmdline with a bad size"""
        try:
            cmdline = shlex.split("msrsync -s abcde src dest")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")

    def test_badsize2(self):
        """ parse cmdline with a bad size"""
        try:
            cmdline = shlex.split("msrsync -s abcde src dest")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")


    def test_bad_filesnumber(self):
        """ parse cmdline with a bad size"""
        try:
            cmdline = shlex.split("msrsync -f abcde src dest")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")

    def test_only_src(self):
        """ parse cmdline with only a source dir"""
        try:
            cmdline = shlex.split("msrsync src")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")

    def test_src_dst(self):
        """ test a basic and valid command line """
        cmdline = shlex.split("msrsync src dst")
        opt, srcs, dst = parse_cmdline(cmdline)
        self.assertEqual(opt.rsync, DEFAULT_RSYNC_OPTIONS)
        self.assertEqual(srcs, ["src"])
        self.assertEqual(dst, "dst")

    def test_src_multiple_dst(self):
        """ test a command line with multiple sources """
        cmdline = shlex.split("msrsync src1 src2 dst")
        opt, srcs, dst = parse_cmdline(cmdline)
        self.assertEqual(opt.rsync, DEFAULT_RSYNC_OPTIONS)
        self.assertEqual(srcs, ["src1", "src2"])
        self.assertEqual(dst, "dst")

    def test_src_dst_rsync(self):
        """ test a basic and valid command line with rsync option """
        cmdline = shlex.split("""msrsync --rsync "--numeric-ids" src dst""")
        opt, srcs, dst = parse_cmdline(cmdline)
        self.assertEqual(opt.rsync, "--numeric-ids")
        self.assertEqual(srcs, ["src"])
        self.assertEqual(dst, "dst")

    def test_src_multiple_dst_rsync(self):
        """ test a command line with multiple sources """
        cmdline = shlex.split("""msrsync --rsync "--numeric-ids" src1 src2 dst""")
        opt, srcs, dst = parse_cmdline(cmdline)
        self.assertEqual(opt.rsync, "--numeric-ids")
        self.assertEqual(srcs, ["src1", "src2"])
        self.assertEqual(dst, "dst")

    def test_src_dest_empty_rsync(self):
        """ test a basic and valid command line, but with empty rsync option """
        try:
            cmdline = shlex.split("msrsync --rsync src dst")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")

    def test_rsync_delete(self):
        """ command line with --rsync option that contains --delete """
        try:
            cmdline = shlex.split("""msrsync --rsync "--delete" dst""")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")

    def test_rsync_delete2(self):
        """ command line with --rsync option that contains --delete """
        try:
            cmdline = shlex.split("""msrsync --rsync "-a --numeric-ids --delete" src dst""")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")

    def test_rsync_delete3(self):
        """ command line with -r option that contains --delete """
        try:
            cmdline = shlex.split("""msrsync -r "-a --numeric-ids --delete" src dst""")
            parse_cmdline(cmdline)
        except SystemExit, err:
            self.assertEqual(err.code, EOPTION_PARSER)
            return

        self.fail("Should have raised a SystemExit exception")


class TestRsyncOptionsChecker(unittest.TestCase):
    """
    Test the rsync options checker
    """
    # pylint: disable=too-many-public-methods
    def test_rsync_wrong_options(self):
        """ test with wrong_options """
        try:
            rsync_options = "--this-is-fake"
            _check_rsync_options(rsync_options)
        except SystemExit, err:
            self.assertEqual(err.code, ERSYNC_OPTIONS_CHECK)
            return

        self.fail("Should have raised a SystemExit exception")


class TestSyncAPI(unittest.TestCase):
    """
    Test msrsync by directly calling python function
    It is redondant with TestSyncCLI but it makes coverage.py happy =)
    """

    # pylint: disable=too-many-public-methods
    def setUp(self):
        """ create a temporary fake tree """
        _check_executables()
        self.src = tempfile.mkdtemp(prefix='msrsync_testsync_')
        self.dst = tempfile.mkdtemp(prefix='msrsync_testsync_')
        _create_fake_tree(self.src, total_entries=1234, max_entries_per_level=123, max_depth=5, files_pct=95)

    def tearDown(self):
        """ remove the temporary fake tree """
        for path in self.src, self.dst:
            if os.path.exists(path):
                shutil.rmtree(path, onerror=rmtree_onerror)

    def _msrsync_test_helper(self, options=""):
        """ msrsync test helper """
        cmdline = """msrsync %s %s %s""" % (options, self.src, self.dst)
        main(shlex.split(cmdline))
        self.assert_(_compare_trees(self.src, self.dst), "The source %s and destination %s tree are not equal." % (self.src, self.dst))

    def test_simple_msrsync_api(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper()

    def test_msrsync_api_2_processes(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper(options='-p 2')

    def test_msrsync_api_4_processes(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper(options='-p 4')

    def test_msrsync_api_8_processes(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper(options='-p 8')



class TestSyncCLI(unittest.TestCase):
    """
    Test the synchronisation process using the commmand line interface
    """

    # pylint: disable=too-many-public-methods
    def setUp(self):
        """ create a temporary fake tree """
        _check_executables()
        self.src = tempfile.mkdtemp(prefix='msrsync_testsync_')
        self.dst = tempfile.mkdtemp(prefix='msrsync_testsync_')
        _create_fake_tree(self.src, total_entries=1234, max_entries_per_level=123, max_depth=5, files_pct=95)

    def tearDown(self):
        """ remove the temporary fake tree """
        for path in self.src, self.dst:
            if os.path.exists(path):
                shutil.rmtree(path, onerror=rmtree_onerror)

    def _msrsync_test_helper(self, options=""):
        """ msrsync test helper """
        cmd = "%s %s %s %s" % (os.path.realpath(__file__), options, self.src + os.sep, self.dst)
        ret, _, _, timeout, _ = run(cmd, timeout_sec=60)
        self.assert_(not timeout, "The msrsync command has timeouted.")
        self.assertEqual(ret, 0, "The msrsync command has failed.")
        self.assert_(_compare_trees(self.src, self.dst), "The source %s and destination %s tree are not equal." % (self.src, self.dst))


    def test_simple_rsync(self):
        """ test simple rsync synchronisation """
        cmd = "%s %s %s %s" % (RSYNC_EXE, DEFAULT_RSYNC_OPTIONS, self.src + os.sep, self.dst)
        ret, _, _, timeout, _ = run(cmd, timeout_sec=60)
        self.assert_(not timeout, "The rsync command has timeouted.")
        self.assertEqual(ret, 0, "The rsync command has failed.")
        self.assert_(_compare_trees(self.src, self.dst), "The source and destination tree are not equal. %s %s" % (self.src, self.dst))

    def test_simple_msrsync_cli(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper()

    def test_simple_msrsync_progress_cli(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper(options='--progress')

    def test_msrsync_progress_cli_2_processes(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper(options='--progress -p 2')

    def test_msrsync_cli_2_processes(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper(options='-p 2')

    def test_msrsync_cli_4_processes(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper(options='-p 4')

    def test_msrsync_cli_8_processes(self):
        """ test simple msrsync synchronisation """
        self._msrsync_test_helper(options='-p 8')


def selftest():
    """
    Embedded testing runner
    """
    suite = unittest.TestSuite()

    tests = [TestHelpers, \
             TestOptionsParser, \
             TestRsyncOptionsChecker, \
             TestSyncAPI, \
             TestSyncCLI]

    for test in tests:
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(test))

    unittest.TextTestRunner(verbosity=2).run(suite)


def _check_root(msg=None):
    """ Check if the caller is running under root """
    msg = "Need to be root" if not msg else msg
    if os.geteuid() != 0:
        print >>sys.stderr, "You're not root. Buffer cache will not be dropped between run. Take the result with caution."
    return True


def drop_caches(value=3):
    """ Drop caches using /proc/sys/vm/drop_caches """
    if os.geteuid() != 0:
        return
    drop_caches_path = "/proc/sys/vm/drop_caches"
    if os.path.exists(drop_caches_path):
        with open(drop_caches_path, "w") as proc_file:
            proc_file.write(str(value))
    else:
        print >>sys.stderr, "/proc/sys/vm/drop_caches does not exist. Cannot drop buffer cache"


def bench(total_entries=10000, max_entries_per_level=128, max_depth=5, files_pct=95, src=None, dst=None):
    """
    Embedded benchmark runner
    """
    # pylint: disable=too-many-arguments
    def _run_or_die(cmd):
        """ helper """
        ret, _, stderr, timeout, elapsed = run(cmd, timeout_sec=900)
        if ret == 666:
            sys.exit(EMSRSYNC_INTERRUPTED)
        if ret != 0 or timeout:
            print >>sys.stderr, "Problem running %s, aborting benchmark: %s" % (cmd, stderr)
            sys.exit(EBENCH)
        return elapsed

    def _run_msrsync_bench_and_print(options, src, dst, reference_result):
        """ helper """
        cmd = "%s %s %s %s" % (os.path.realpath(__file__), options, src, dst)
        msrsync_elapsed = _run_or_die(cmd)
        print >>sys.stdout, "msrsync %s took %.2f seconds (speedup x%.2f)" % (options, msrsync_elapsed, reference_result/msrsync_elapsed)


    _check_executables()
    cleanup_src = cleanup_dst = False
    try:
        if src is None:
            src = tempfile.mkdtemp()
            cleanup_src = True

        if dst is None:
            dst = tempfile.mkdtemp()
            cleanup_dst = True

        # to remove the directory between run
        dst_in_dst = tempfile.mkdtemp(dir=dst)

        _create_fake_tree(src, total_entries=total_entries, max_entries_per_level=max_entries_per_level, max_depth=max_depth, files_pct=files_pct)

        print >>sys.stdout, "Benchmarks with %d entries (%d%% of files):" % (total_entries, files_pct)

        shutil.rmtree(dst_in_dst, onerror=rmtree_onerror)
        drop_caches()

        cmd = "%s %s %s %s" % (RSYNC_EXE, DEFAULT_RSYNC_OPTIONS, src + os.sep, dst_in_dst)
        rsync_elapsed = _run_or_die(cmd)
        print >>sys.stdout, "rsync %s took %.2f seconds (speedup x1)" % (DEFAULT_RSYNC_OPTIONS, rsync_elapsed)

        shutil.rmtree(dst_in_dst, onerror=rmtree_onerror)
        drop_caches()

        _run_msrsync_bench_and_print('--processes 1 --files 1000 --size 1G', src + os.sep, dst_in_dst, rsync_elapsed)

        shutil.rmtree(dst_in_dst, onerror=rmtree_onerror)
        drop_caches()

        _run_msrsync_bench_and_print('--processes 2 --files 1000 --size 1G', src + os.sep, dst_in_dst, rsync_elapsed)

        shutil.rmtree(dst_in_dst, onerror=rmtree_onerror)
        drop_caches()

        _run_msrsync_bench_and_print('--processes 4 --files 1000 --size 1G', src + os.sep, dst_in_dst, rsync_elapsed)

        shutil.rmtree(dst_in_dst, onerror=rmtree_onerror)
        drop_caches()

        _run_msrsync_bench_and_print('--processes 8 --files 1000 --size 1G', src + os.sep, dst_in_dst, rsync_elapsed)

        shutil.rmtree(dst_in_dst, onerror=rmtree_onerror)
        drop_caches()

        _run_msrsync_bench_and_print('--processes 16 --files 1000 --size 1G', src + os.sep, dst_in_dst, rsync_elapsed)

    finally:
        if cleanup_src and os.path.exists(src):
            shutil.rmtree(src, onerror=rmtree_onerror)
        if cleanup_dst and os.path.exists(dst):
            shutil.rmtree(dst, onerror=rmtree_onerror)


def benchshm(total_entries=10000, max_entries_per_level=128, max_depth=5, files_pct=95):
    """
    Embedded benchmark runner
    """
    try:
        shm = os.getenv("SHM", "/dev/shm")
        src = tempfile.mkdtemp(dir=shm)
        dst = tempfile.mkdtemp(dir=shm)
    except OSError, err:
        print >>sys.stderr, "Error creating temporary bench directories in %s: %s" % (shm, err)
        sys.exit(EBENCH)

    try:
        bench(total_entries=total_entries, max_entries_per_level=max_entries_per_level, max_depth=max_depth, files_pct=files_pct, src=dst, dst=dst)
    finally:
        if os.path.exists(src):
            shutil.rmtree(src, onerror=rmtree_onerror)
        if os.path.exists(dst):
            shutil.rmtree(dst, onerror=rmtree_onerror)


def main(cmdline):
    """
    main
    """
    _check_python_version()
    options, srcs, dest = parse_cmdline(cmdline)

    if options.version:
        if USING_SCANDIR:
            print >>sys.stdout, "%s" % VERSION
        else:
            print >>sys.stdout, "%s (no scandir optimization. Use python 3.5+ or install the scandir module)" % VERSION
        sys.exit(0)

    if options.selftest:
        selftest()
        sys.exit(0)

    if options.bench and _check_root():
        bench(total_entries=100000)
        sys.exit(0)

    if options.benchshm and _check_root():
        benchshm(total_entries=100000)
        sys.exit(0)

    _check_executables()
    _check_srcs_dest(srcs, dest)
    _check_rsync_options(options.rsync)
    return msrsync(options, srcs, dest)


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except (KeyboardInterrupt, SystemExit):
        pass