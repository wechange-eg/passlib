"""passlib.handlers.argon2 -- argon2 password hash wrapper

References
==========
* argon2
    - home: https://github.com/P-H-C/phc-winner-argon2
    - whitepaper: https://github.com/P-H-C/phc-winner-argon2/blob/master/argon2-specs.pdf
* argon2 cffi wrapper
    - pypi: https://pypi.python.org/pypi/argon2_cffi
    - home: https://github.com/hynek/argon2_cffi
* argon2 pure python
    - pypi: https://pypi.python.org/pypi/argon2pure
    - home: https://github.com/bwesterb/argon2pure
"""
#=============================================================================
# imports
#=============================================================================
# core
import logging
log = logging.getLogger(__name__)
import re
import types
from warnings import warn
# site
_argon2_cffi = None  # loaded below
_argon2pure = None  # dynamically imported by _load_backend_argon2pure()
# pkg
from passlib import exc
from passlib.crypto.digest import MAX_UINT32
from passlib.utils import classproperty, to_bytes, render_bytes
from passlib.utils.binary import b64s_encode, b64s_decode
from passlib.utils.compat import bascii_to_str
import passlib.utils.handlers as uh
# local
__all__ = [
    "argon2",
]

#=============================================================================
# helpers
#=============================================================================

# NOTE: when adding a new argon2 hash type, need to do the following:
# * add TYPE_XXX constant, and add to ALL_TYPES
# * make sure "_backend_type_map" constructors handle it correctly for all backends
# * make sure _hash_regex & _ident_regex (below) support type string.
# * add reference vectors for testing.

#: argon2 type constants -- subclasses handle mapping these to backend-specific type constants.
#: (should be lowercase, to match representation in hash string)
TYPE_I = u"i"
TYPE_D = u"d"
TYPE_ID = u"id"  # new 2016-10-29; passlib 1.7.2 requires backends new enough for support

#: list of all known types; first (supported) type will be used as default.
ALL_TYPES = (TYPE_ID, TYPE_I, TYPE_D)
ALL_TYPES_SET = set(ALL_TYPES)

#=============================================================================
# import argon2 package (https://pypi.python.org/pypi/argon2_cffi)
#=============================================================================

# import cffi package
# NOTE: we try to do this even if caller is going to use argon2pure,
#       so that we can always use the libargon2 default settings when possible.
_argon2_cffi_error = None
try:
    import argon2 as _argon2_cffi
except ImportError:
    _argon2_cffi = None
else:
    if not hasattr(_argon2_cffi, "Type"):
        # they have incompatible "argon2" package installed, instead of "argon2_cffi" package.
        _argon2_cffi_error = (
            "'argon2' module points to unsupported 'argon2' pypi package; "
            "please install 'argon2-cffi' instead."
        )
        _argon2_cffi = None
    elif not hasattr(_argon2_cffi, "low_level"):
        # they have pre-v16 argon2_cffi package
        _argon2_cffi_error = "'argon2-cffi' is too old, please update to argon2_cffi >= 18.2.0"
        _argon2_cffi = None

# init default settings for our hasher class --
# if we have argon2_cffi >= 16.0, use their default hasher settings, otherwise use static default
if hasattr(_argon2_cffi, "PasswordHasher"):
    # use cffi's default settings
    _default_settings = _argon2_cffi.PasswordHasher()
    _default_version = _argon2_cffi.low_level.ARGON2_VERSION
else:
    # use fallback settings (for no backend, or argon2pure)
    class _DummyCffiHasher:
        """
        dummy object to use as source of defaults when argon2_cffi isn't present.
        this tries to mimic the attributes of ``argon2.PasswordHasher()`` which the rest of
        this module reads.

        .. note:: values last synced w/ argon2 19.2 as of 2019-11-09
        """
        time_cost = 2
        memory_cost = 512
        parallelism = 2
        salt_len = 16
        hash_len = 16
        # NOTE: "type" attribute added in argon2_cffi v18.2; but currently not reading it
        # type = _argon2_cffi.Type.ID

    _default_settings = _DummyCffiHasher()
    _default_version = 0x13  # v1.9

#=============================================================================
# handler
#=============================================================================
class _Argon2Common(uh.SubclassBackendMixin, uh.ParallelismMixin,
                    uh.HasRounds, uh.HasRawSalt, uh.HasRawChecksum,
                    uh.GenericHandler):
    """
    Base class which implements brunt of Argon2 code.
    This is then subclassed by the various backends,
    to override w/ backend-specific methods.

    When a backend is loaded, the bases of the 'argon2' class proper
    are modified to prepend the correct backend-specific subclass.
    """
    #===================================================================
    # class attrs
    #===================================================================

    #------------------------
    # PasswordHash
    #------------------------

    name = "argon2"
    setting_kwds = ("salt",
                    "salt_size",
                    "salt_len",  # 'salt_size' alias for compat w/ argon2 package
                    "rounds",
                    "time_cost",  # 'rounds' alias for compat w/ argon2 package
                    "memory_cost",
                    "parallelism",
                    "digest_size",
                    "hash_len",  # 'digest_size' alias for compat w/ argon2 package
                    "type",  # the type of argon2 hash used
                    )

    # TODO: could support the optional 'data' parameter,
    #       but need to research the uses, what a more descriptive name would be,
    #       and deal w/ fact that argon2_cffi 16.1 doesn't currently support it.
    #       (argon2_pure does though)

    #------------------------
    # GenericHandler
    #------------------------

    # NOTE: ident -- all argon2 hashes start with "$argon2<type>$"
    # XXX: could programmaticaly generate "ident_values" string from ALL_TYPES above

    checksum_size = _default_settings.hash_len

    #: force parsing these kwds
    _always_parse_settings = uh.GenericHandler._always_parse_settings + \
                             ("type",)

    #: exclude these kwds from parsehash() result (most are aliases for other keys)
    _unparsed_settings = uh.GenericHandler._unparsed_settings + \
                         ("salt_len", "time_cost", "hash_len", "digest_size")

    #------------------------
    # HasSalt
    #------------------------
    default_salt_size = _default_settings.salt_len
    min_salt_size = 8
    max_salt_size = MAX_UINT32

    #------------------------
    # HasRounds
    # TODO: once rounds limit logic is factored out,
    #       make 'rounds' and 'cost' an alias for 'time_cost'
    #------------------------
    default_rounds = _default_settings.time_cost
    min_rounds = 1
    max_rounds = MAX_UINT32
    rounds_cost = "linear"

    #------------------------
    # ParalleismMixin
    #------------------------
    max_parallelism = (1 << 24) - 1  # from argon2.h / ARGON2_MAX_LANES

    #------------------------
    # custom
    #------------------------

    #: max version support
    #: NOTE: this is dependant on the backend, and initialized/modified by set_backend()
    max_version = _default_version

    #: minimum version before needs_update() marks the hash; if None, defaults to max_version
    min_desired_version = None

    #: minimum valid memory_cost
    min_memory_cost = 8  # from argon2.h / ARGON2_MIN_MEMORY

    #: maximum number of threads (-1=unlimited);
    #: number of threads used by .hash() will be min(parallelism, max_threads)
    max_threads = -1

    #: global flag signalling argon2pure backend to use threads
    #: rather than subprocesses.
    pure_use_threads = False

    #: internal helper used to store mapping of TYPE_XXX constants -> backend-specific type constants;
    #: this is populated by _load_backend_mixin(); and used to detect which types are supported.
    #: XXX: could expose keys as class-level .supported_types property?
    _backend_type_map = {}

    @classproperty
    def type_values(cls):
        """
        return tuple of types supported by this backend
        
        .. versionadded:: 1.7.2
        """
        cls.get_backend()  # make sure backend is loaded
        return tuple(cls._backend_type_map)

    #===================================================================
    # instance attrs
    #===================================================================

    #: argon2 hash type, one of ALL_TYPES -- class value controls the default
    #: .. versionadded:: 1.7.2
    type = TYPE_ID

    #: parallelism setting -- class value controls the default
    parallelism = _default_settings.parallelism

    #: hash version (int)
    #: NOTE: this is modified by set_backend()
    version = _default_version

    #: memory cost -- class value controls the default
    memory_cost = _default_settings.memory_cost

    @property
    def type_d(self):
        """
        flag indicating a Type D hash

        .. deprecated:: 1.7.2; will be removed in passlib 2.0
        """
        return self.type == TYPE_D

    #: optional secret data
    data = None

    #===================================================================
    # variant constructor
    #===================================================================

    @classmethod
    def using(cls, type=None, memory_cost=None, salt_len=None, time_cost=None, digest_size=None,
              checksum_size=None, hash_len=None, max_threads=None, **kwds):
        # support aliases which match argon2 naming convention
        if time_cost is not None:
            if "rounds" in kwds:
                raise TypeError("'time_cost' and 'rounds' are mutually exclusive")
            kwds['rounds'] = time_cost

        if salt_len is not None:
            if "salt_size" in kwds:
                raise TypeError("'salt_len' and 'salt_size' are mutually exclusive")
            kwds['salt_size'] = salt_len

        if hash_len is not None:
            if digest_size is not None:
                raise TypeError("'hash_len' and 'digest_size' are mutually exclusive")
            digest_size = hash_len

        if checksum_size is not None:
            if digest_size is not None:
                raise TypeError("'checksum_size' and 'digest_size' are mutually exclusive")
            digest_size = checksum_size

        # create variant
        subcls = super(_Argon2Common, cls).using(**kwds)

        # set type
        if type is not None:
            subcls.type = subcls._norm_type(type)

        # set checksum size
        relaxed = kwds.get("relaxed")
        if digest_size is not None:
            if isinstance(digest_size, str):
                digest_size = int(digest_size)
            # NOTE: this isn't *really* digest size minimum, but want to enforce secure minimum.
            subcls.checksum_size = uh.norm_integer(subcls, digest_size, min=16, max=MAX_UINT32,
                                                   param="digest_size", relaxed=relaxed)

        # set memory cost
        if memory_cost is not None:
            if isinstance(memory_cost, str):
                memory_cost = int(memory_cost)
            subcls.memory_cost = subcls._norm_memory_cost(memory_cost, relaxed=relaxed)

        # validate constraints
        subcls._validate_constraints(subcls.memory_cost, subcls.parallelism)

        # set max threads
        if max_threads is not None:
            if isinstance(max_threads, str):
                max_threads = int(max_threads)
            if max_threads < 1 and max_threads != -1:
                raise ValueError("max_threads (%d) must be -1 (unlimited), or at least 1." %
                                 (max_threads,))
            subcls.max_threads = max_threads

        return subcls

    @classmethod
    def _validate_constraints(cls, memory_cost, parallelism):
        # NOTE: this is used by class & instance, hence passing in via arguments.
        #       could switch and make this a hybrid method.
        min_memory_cost = 8 * parallelism
        if memory_cost < min_memory_cost:
            raise ValueError("%s: memory_cost (%d) is too low, must be at least "
                             "8 * parallelism (8 * %d = %d)" %
                             (cls.name, memory_cost,
                              parallelism, min_memory_cost))

    #===================================================================
    # public api
    #===================================================================

    #: shorter version of _hash_regex, used to quickly identify hashes
    _ident_regex = re.compile(r"^\$argon2[a-z]+\$")

    @classmethod
    def identify(cls, hash):
        hash = uh.to_unicode_for_identify(hash)
        return cls._ident_regex.match(hash) is not None

    # hash(), verify(), genhash() -- implemented by backend subclass

    #===================================================================
    # hash parsing / rendering
    #===================================================================

    # info taken from source of decode_string() function in
    # <https://github.com/P-H-C/phc-winner-argon2/blob/master/src/encoding.c>
    #
    # hash format:
    #   $argon2<T>[$v=<num>]$m=<num>,t=<num>,p=<num>[,keyid=<bin>][,data=<bin>][$<bin>[$<bin>]]
    #
    # NOTE: as of 2016-6-17, the official source (above) lists the "keyid" param in the comments,
    #       but the actual source of decode_string & encode_string don't mention it at all.
    #       we're supporting parsing it, but throw NotImplementedError if encountered.
    #
    # sample hashes:
    #    v1.0: '$argon2i$m=512,t=2,p=2$5VtWOO3cGWYQHEMaYGbsfQ$AcmqasQgW/wI6wAHAMk4aQ'
    #    v1.3: '$argon2i$v=19$m=512,t=2,p=2$5VtWOO3cGWYQHEMaYGbsfQ$AcmqasQgW/wI6wAHAMk4aQ'

    #: regex to parse argon hash
    _hash_regex = re.compile(br"""
        ^
        \$argon2(?P<type>[a-z]+)\$
        (?:
            v=(?P<version>\d+)
            \$
        )?
        m=(?P<memory_cost>\d+)
        ,
        t=(?P<time_cost>\d+)
        ,
        p=(?P<parallelism>\d+)
        (?:
            ,keyid=(?P<keyid>[^,$]+)
        )?
        (?:
            ,data=(?P<data>[^,$]+)
        )?
        (?:
            \$
            (?P<salt>[^$]+)
            (?:
                \$
                (?P<digest>.+)
            )?
        )?
        $
    """, re.X)

    @classmethod
    def from_string(cls, hash):
        # NOTE: assuming hash will be str, or use ascii-compatible encoding.
        # TODO: switch to working w/ str
        if isinstance(hash, str):
            hash = hash.encode("utf-8")
        if not isinstance(hash, bytes):
            raise exc.ExpectedStringError(hash, "hash")
        m = cls._hash_regex.match(hash)
        if not m:
            raise exc.MalformedHashError(cls)
        type, version, memory_cost, time_cost, parallelism, keyid, data, salt, digest = \
            m.group("type", "version", "memory_cost", "time_cost", "parallelism",
                    "keyid", "data", "salt", "digest")
        if keyid:
            raise NotImplementedError("argon2 'keyid' parameter not supported")
        return cls(
            type=type.decode("ascii"),
            version=int(version) if version else 0x10,
            memory_cost=int(memory_cost),
            rounds=int(time_cost),
            parallelism=int(parallelism),
            salt=b64s_decode(salt) if salt else None,
            data=b64s_decode(data) if data else None,
            checksum=b64s_decode(digest) if digest else None,
        )

    def to_string(self):
        version = self.version
        if version == 0x10:
            vstr = ""
        else:
            vstr = "v=%d$" % version

        data = self.data
        if data:
            kdstr = ",data=" + bascii_to_str(b64s_encode(self.data))
        else:
            kdstr = ""

        # NOTE: 'keyid' param currently not supported
        return "$argon2%s$%sm=%d,t=%d,p=%d%s$%s$%s" % (
            self.type,
            vstr, 
            self.memory_cost,
            self.rounds, 
            self.parallelism,
            kdstr,
            bascii_to_str(b64s_encode(self.salt)),
            bascii_to_str(b64s_encode(self.checksum)),
        )

    #===================================================================
    # init
    #===================================================================
    def __init__(self, type=None, type_d=False, version=None, memory_cost=None, data=None, **kwds):

        # handle deprecated kwds
        if type_d:
            warn('argon2 `type_d=True` keyword is deprecated, and will be removed in passlib 2.0; '
                 'please use ``type="d"`` instead')
            assert type is None
            type = TYPE_D

        # TODO: factor out variable checksum size support into a mixin.
        # set checksum size to specific value before _norm_checksum() is called
        checksum = kwds.get("checksum")
        if checksum is not None:
            self.checksum_size = len(checksum)

        # call parent
        super(_Argon2Common, self).__init__(**kwds)

        # init type
        if type is None:
            assert uh.validate_default_value(self, self.type, self._norm_type, param="type")
        else:
            self.type = self._norm_type(type)

        # init version
        if version is None:
            assert uh.validate_default_value(self, self.version, self._norm_version,
                                             param="version")
        else:
            self.version = self._norm_version(version)

        # init memory cost
        if memory_cost is None:
            assert uh.validate_default_value(self, self.memory_cost, self._norm_memory_cost,
                                             param="memory_cost")
        else:
            self.memory_cost = self._norm_memory_cost(memory_cost)

        # init data
        if data is None:
            assert self.data is None
        else:
            if not isinstance(data, bytes):
                raise uh.exc.ExpectedTypeError(data, "bytes", "data")
            self.data = data

    #-------------------------------------------------------------------
    # parameter guards
    #-------------------------------------------------------------------

    @classmethod
    def _norm_type(cls, value):
        # type check
        if not isinstance(value, str):
            raise uh.exc.ExpectedTypeError(value, "str", "type")

        # check if type is valid
        if value in ALL_TYPES_SET:
            return value

        # translate from uppercase
        temp = value.lower()
        if temp in ALL_TYPES_SET:
            return temp

        # failure!
        raise ValueError("unknown argon2 hash type: %r" % (value,))

    @classmethod
    def _norm_version(cls, version):
        if not isinstance(version, int):
            raise uh.exc.ExpectedTypeError(version, "integer", "version")

        # minimum valid version
        if version < 0x13 and version != 0x10:
            raise ValueError("invalid argon2 hash version: %d" % (version,))

        # check this isn't past backend's max version
        backend = cls.get_backend()
        if version > cls.max_version:
            raise ValueError("%s: hash version 0x%X not supported by %r backend "
                             "(max version is 0x%X); try updating or switching backends" %
                             (cls.name, version, backend, cls.max_version))
        return version

    @classmethod
    def _norm_memory_cost(cls, memory_cost, relaxed=False):
        return uh.norm_integer(cls, memory_cost, min=cls.min_memory_cost,
                               param="memory_cost", relaxed=relaxed)

    #===================================================================
    # digest calculation
    #===================================================================

    # NOTE: _calc_checksum implemented by backend subclass

    @classmethod
    def _get_backend_type(cls, value):
        """
        helper to resolve backend constant from type
        """
        try:
            return cls._backend_type_map[value]
        except KeyError:
            pass
        # XXX: pick better error class?
        msg = "unsupported argon2 hash (type %r not supported by %s backend)" % \
              (value, cls.get_backend())
        raise ValueError(msg)

    #===================================================================
    # hash migration
    #===================================================================

    def _calc_needs_update(self, **kwds):
        cls = type(self)
        if self.type != cls.type:
            return True
        minver = cls.min_desired_version
        if minver is None or minver > cls.max_version:
            minver = cls.max_version
        if self.version < minver:
            # version is too old.
            return True
        if self.memory_cost != cls.memory_cost:
            return True
        if self.checksum_size != cls.checksum_size:
            return True
        return super(_Argon2Common, self)._calc_needs_update(**kwds)
    
    #===================================================================
    # backend loading
    #===================================================================

    _no_backend_suggestion = " -- recommend you install one (e.g. 'pip install argon2_cffi')"

    @classmethod
    def _finalize_backend_mixin(mixin_cls, name, dryrun):
        """
        helper called by from backend mixin classes' _load_backend_mixin() --
        invoked after backend imports have been loaded, and performs
        feature detection & testing common to all backends.
        """
        # check argon2 version
        max_version = mixin_cls.max_version
        assert isinstance(max_version, int) and max_version >= 0x10
        if max_version < 0x13:
            warn("%r doesn't support argon2 v1.3, and should be upgraded" % name,
                 uh.exc.PasslibSecurityWarning)

        # prefer best available type
        for type in ALL_TYPES:
            if type in mixin_cls._backend_type_map:
                mixin_cls.type = type
                break
        else:
            warn("%r lacks support for all known hash types" % name, uh.exc.PasslibRuntimeWarning)
            # NOTE: class will just throw "unsupported argon2 hash" error if they try to use it...
            mixin_cls.type = TYPE_ID

        return True

    @classmethod
    def _adapt_backend_error(cls, err, hash=None, self=None):
        """
        internal helper invoked when backend has hash/verification error;
        used to adapt to passlib message.
        """
        backend = cls.get_backend()

        # parse hash to throw error if format was invalid, parameter out of range, etc.
        if self is None and hash is not None:
            self = cls.from_string(hash)

        # check constraints on parsed object
        # XXX: could move this to __init__, but not needed by needs_update calls
        if self is not None:
            self._validate_constraints(self.memory_cost, self.parallelism)

            # as of cffi 16.1, lacks support in hash_secret(), so genhash() will get here.
            # as of cffi 16.2, support removed from verify_secret() as well.
            if backend == "argon2_cffi" and self.data is not None:
                raise NotImplementedError("argon2_cffi backend doesn't support the 'data' parameter")

        # fallback to reporting a malformed hash
        text = str(err)
        if text not in [
            "Decoding failed"  # argon2_cffi's default message
            ]:
            reason = "%s reported: %s: hash=%r" % (backend, text, hash)
        else:
            reason = repr(hash)
        raise exc.MalformedHashError(cls, reason=reason)

    #===================================================================
    # eoc
    #===================================================================

#-----------------------------------------------------------------------
# stub backend
#-----------------------------------------------------------------------
class _NoBackend(_Argon2Common):
    """
    mixin used before any backend has been loaded.
    contains stubs that force loading of one of the available backends.
    """
    #===================================================================
    # primary methods
    #===================================================================
    @classmethod
    def hash(cls, secret):
        cls._stub_requires_backend()
        return cls.hash(secret)

    @classmethod
    def verify(cls, secret, hash):
        cls._stub_requires_backend()
        return cls.verify(secret, hash)

    @uh.deprecated_method(deprecated="1.7", removed="2.0")
    @classmethod
    def genhash(cls, secret, config):
        cls._stub_requires_backend()
        return cls.genhash(secret, config)

    #===================================================================
    # digest calculation
    #===================================================================
    def _calc_checksum(self, secret):
        # NOTE: since argon2_cffi takes care of rendering hash,
        #       _calc_checksum() is only used by the argon2pure backend.
        self._stub_requires_backend()
        # NOTE: have to use super() here so that we don't recursively
        #       call subclass's wrapped _calc_checksum
        return super(argon2, self)._calc_checksum(secret)

    #===================================================================
    # eoc
    #===================================================================

#-----------------------------------------------------------------------
# argon2_cffi backend
#-----------------------------------------------------------------------
class _CffiBackend(_Argon2Common):
    """
    argon2_cffi backend
    """
    #===================================================================
    # backend loading
    #===================================================================

    @classmethod
    def _load_backend_mixin(mixin_cls, name, dryrun):
        # make sure we write info to base class's __dict__, not that of a subclass
        assert mixin_cls is _CffiBackend

        # we automatically import this at top, so just grab info
        if _argon2_cffi is None:
            if _argon2_cffi_error:
                raise exc.PasslibSecurityError(_argon2_cffi_error)
            return False
        max_version = _argon2_cffi.low_level.ARGON2_VERSION
        log.debug("detected 'argon2_cffi' backend, version %r, with support for 0x%x argon2 hashes",
                  _argon2_cffi.__version__, max_version)

        # build type map
        TypeEnum = _argon2_cffi.Type
        type_map = {}
        for type in ALL_TYPES:
            try:
                type_map[type] = getattr(TypeEnum, type.upper())
            except AttributeError:
                # TYPE_ID support not added until v18.2
                assert type not in (TYPE_I, TYPE_D), "unexpected missing type: %r" % type
        mixin_cls._backend_type_map = type_map

        # set version info, and run common setup
        mixin_cls.version = mixin_cls.max_version = max_version
        return mixin_cls._finalize_backend_mixin(name, dryrun)

    #===================================================================
    # primary methods
    #===================================================================
    @classmethod
    def hash(cls, secret):
        # TODO: add in 'encoding' support once that's finalized in 1.8 / 1.9.
        uh.validate_secret(secret)
        secret = to_bytes(secret, "utf-8")
        # XXX: doesn't seem to be a way to make this honor max_threads
        try:
            return bascii_to_str(_argon2_cffi.low_level.hash_secret(
                type=cls._get_backend_type(cls.type),
                memory_cost=cls.memory_cost,
                time_cost=cls.default_rounds,
                parallelism=cls.parallelism,
                salt=to_bytes(cls._generate_salt()),
                hash_len=cls.checksum_size,
                secret=secret,
            ))
        except _argon2_cffi.exceptions.HashingError as err:
            raise cls._adapt_backend_error(err)

    #: helper for verify() method below -- maps prefixes to type constants
    _byte_ident_map = dict((render_bytes(b"$argon2%s$", type.encode("ascii")), type)
                           for type in ALL_TYPES)

    @classmethod
    def verify(cls, secret, hash):
        # TODO: add in 'encoding' support once that's finalized in 1.8 / 1.9.
        uh.validate_secret(secret)
        secret = to_bytes(secret, "utf-8")
        hash = to_bytes(hash, "ascii")

        # read type from start of hash
        # NOTE: don't care about malformed strings, lowlevel will throw error for us
        type = cls._byte_ident_map.get(hash[:1+hash.find(b"$", 1)], TYPE_I)
        type_code = cls._get_backend_type(type)

        # XXX: doesn't seem to be a way to make this honor max_threads
        try:
            result = _argon2_cffi.low_level.verify_secret(hash, secret, type_code)
            assert result is True
            return True
        except _argon2_cffi.exceptions.VerifyMismatchError:
            return False
        except _argon2_cffi.exceptions.VerificationError as err:
            raise cls._adapt_backend_error(err, hash=hash)

    # NOTE: deprecated, will be removed in 2.0
    @classmethod
    def genhash(cls, secret, config):
        # TODO: add in 'encoding' support once that's finalized in 1.8 / 1.9.
        uh.validate_secret(secret)
        secret = to_bytes(secret, "utf-8")
        self = cls.from_string(config)
        # XXX: doesn't seem to be a way to make this honor max_threads
        try:
            result = bascii_to_str(_argon2_cffi.low_level.hash_secret(
                type=cls._get_backend_type(self.type),
                memory_cost=self.memory_cost,
                time_cost=self.rounds,
                parallelism=self.parallelism,
                salt=to_bytes(self.salt),
                hash_len=self.checksum_size,
                secret=secret,
                version=self.version,
            ))
        except _argon2_cffi.exceptions.HashingError as err:
            raise cls._adapt_backend_error(err, hash=config)
        if self.version == 0x10:
            # workaround: argon2 0x13 always returns "v=" segment, even for 0x10 hashes
            result = result.replace("$v=16$", "$")
        return result

    #===================================================================
    # digest calculation
    #===================================================================
    def _calc_checksum(self, secret):
        raise AssertionError("shouldn't be called under argon2_cffi backend")

    #===================================================================
    # eoc
    #===================================================================

#-----------------------------------------------------------------------
# argon2pure backend
#-----------------------------------------------------------------------
class _PureBackend(_Argon2Common):
    """
    argon2pure backend
    """
    #===================================================================
    # backend loading
    #===================================================================

    @classmethod
    def _load_backend_mixin(mixin_cls, name, dryrun):
        # make sure we write info to base class's __dict__, not that of a subclass
        assert mixin_cls is _PureBackend

        # import argon2pure
        global _argon2pure
        try:
            import argon2pure as _argon2pure
        except ImportError:
            return False

        # get default / max supported version -- added in v1.2.2
        try:
            from argon2pure import ARGON2_DEFAULT_VERSION as max_version
        except ImportError:
            log.warning("detected 'argon2pure' backend, but package is too old "
                        "(passlib requires argon2pure >= 1.2.3)")
            return False

        log.debug("detected 'argon2pure' backend, with support for 0x%x argon2 hashes",
                  max_version)

        if not dryrun:
            warn("Using argon2pure backend, which is 100x+ slower than is required "
                 "for adequate security. Installing argon2_cffi (via 'pip install argon2_cffi') "
                 "is strongly recommended", exc.PasslibSecurityWarning)

        # build type map
        type_map = {}
        for type in ALL_TYPES:
            try:
                type_map[type] = getattr(_argon2pure, "ARGON2" + type.upper())
            except AttributeError:
                # TYPE_ID support not added until v1.3
                assert type not in (TYPE_I, TYPE_D), "unexpected missing type: %r" % type
        mixin_cls._backend_type_map = type_map

        mixin_cls.version = mixin_cls.max_version = max_version
        return mixin_cls._finalize_backend_mixin(name, dryrun)

    #===================================================================
    # primary methods
    #===================================================================

    # NOTE: this backend uses default .hash() & .verify() implementations.

    #===================================================================
    # digest calculation
    #===================================================================
    def _calc_checksum(self, secret):
        # TODO: add in 'encoding' support once that's finalized in 1.8 / 1.9.
        uh.validate_secret(secret)
        secret = to_bytes(secret, "utf-8")
        kwds = dict(
            password=secret,
            salt=self.salt,
            time_cost=self.rounds,
            memory_cost=self.memory_cost,
            parallelism=self.parallelism,
            tag_length=self.checksum_size,
            type_code=self._get_backend_type(self.type),
            version=self.version,
        )
        if self.max_threads > 0:
            kwds['threads'] = self.max_threads
        if self.pure_use_threads:
            kwds['use_threads'] = True
        if self.data:
            kwds['associated_data'] = self.data
        # NOTE: should return raw bytes
        # NOTE: this may raise _argon2pure.Argon2ParameterError,
        #       but it if does that, there's a bug in our own parameter checking code.
        try:
            return _argon2pure.argon2(**kwds)
        except _argon2pure.Argon2Error as err:
            raise self._adapt_backend_error(err, self=self)

    #===================================================================
    # eoc
    #===================================================================

class argon2(_NoBackend, _Argon2Common):
    """
    This class implements the Argon2 password hash [#argon2-home]_, and follows the :ref:`password-hash-api`.

    Argon2 supports a variable-length salt, and variable time & memory cost,
    and a number of other configurable parameters.

    The :meth:`~passlib.ifc.PasswordHash.replace` method accepts the following optional keywords:

    :type type: str
    :param type:
        Specify the type of argon2 hash to generate.
        Can be one of "ID", "I", "D".

        This defaults to "ID" if supported by the backend, otherwise "I".

    :type salt: str
    :param salt:
        Optional salt string.
        If specified, the length must be between 0-1024 bytes.
        If not specified, one will be auto-generated (this is recommended).

    :type salt_size: int
    :param salt_size:
        Optional number of bytes to use when autogenerating new salts.

    :type rounds: int
    :param rounds:
        Optional number of rounds to use.
        This corresponds linearly to the amount of time hashing will take.

    :type time_cost: int
    :param time_cost:
        An alias for **rounds**, for compatibility with underlying argon2 library.

    :param int memory_cost:
        Defines the memory usage in kibibytes.
        This corresponds linearly to the amount of memory hashing will take.

    :param int parallelism:
        Defines the parallelization factor.
        *NOTE: this will affect the resulting hash value.*

    :param int digest_size:
        Length of the digest in bytes.

    :param int max_threads:
        Maximum number of threads that will be used.
        -1 means unlimited; otherwise hashing will use ``min(parallelism, max_threads)`` threads.

        .. note::

            This option is currently only honored by the argon2pure backend.

    :type relaxed: bool
    :param relaxed:
        By default, providing an invalid value for one of the other
        keywords will result in a :exc:`ValueError`. If ``relaxed=True``,
        and the error can be corrected, a :exc:`~passlib.exc.PasslibHashWarning`
        will be issued instead. Correctable errors include ``rounds``
        that are too small or too large, and ``salt`` strings that are too long.

    .. versionchanged:: 1.7.2

        Added the "type" keyword, and support for type "D" and "ID" hashes.
        (Prior versions could verify type "D" hashes, but not generate them).

    .. todo::

        * Support configurable threading limits.
    """
    #=============================================================================
    # backend
    #=============================================================================

    # NOTE: the brunt of the argon2 class is implemented in _Argon2Common.
    #       there are then subclass for each backend (e.g. _PureBackend),
    #       these are dynamically prepended to this class's bases
    #       in order to load the appropriate backend.

    #: list of potential backends
    backends = ("argon2_cffi", "argon2pure")

    #: flag that this class's bases should be modified by SubclassBackendMixin
    _backend_mixin_target = True

    #: map of backend -> mixin class, used by _get_backend_loader()
    _backend_mixin_map = {
        None: _NoBackend,
        "argon2_cffi": _CffiBackend,
        "argon2pure": _PureBackend,
    }

    #=============================================================================
    #
    #=============================================================================

#=============================================================================
# eof
#=============================================================================
