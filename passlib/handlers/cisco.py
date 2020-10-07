"""
passlib.handlers.cisco -- Cisco password hashes
"""
#=============================================================================
# imports
#=============================================================================
# core
from binascii import hexlify, unhexlify
from hashlib import md5
import logging; log = logging.getLogger(__name__)
from warnings import warn
# site
# pkg
from passlib.utils import right_pad_string, to_unicode, repeat_string, to_bytes
from passlib.utils.binary import h64
import passlib.utils.handlers as uh
# local
__all__ = [
    "cisco_pix",
    "cisco_asa",
    "cisco_type7",
]

#=============================================================================
# utils
#=============================================================================

#: dummy bytes used by spoil_digest var in cisco_pix._calc_checksum()
_DUMMY_BYTES = b'\xFF' * 32

#=============================================================================
# cisco pix firewall hash
#=============================================================================
class cisco_pix(uh.HasUserContext, uh.StaticHandler):
    """
    This class implements the password hash used by older Cisco PIX firewalls,
    and follows the :ref:`password-hash-api`.
    It does a single round of hashing, and relies on the username
    as the salt.

    This class only allows passwords <= 16 bytes, anything larger
    will result in a :exc:`~passlib.exc.PasswordSizeError` if passed to :meth:`~cisco_pix.hash`,
    and be silently rejected if passed to :meth:`~cisco_pix.verify`.

    The :meth:`~passlib.ifc.PasswordHash.hash`,
    :meth:`~passlib.ifc.PasswordHash.genhash`, and
    :meth:`~passlib.ifc.PasswordHash.verify` methods
    all support the following extra keyword:

    :param str user:
        String containing name of user account this password is associated with.

        This is *required* in order to correctly hash passwords associated
        with a user account on the Cisco device, as it is used to salt
        the hash.

        Conversely, this *must* be omitted or set to ``""`` in order to correctly
        hash passwords which don't have an associated user account
        (such as the "enable" password).

    .. versionadded:: 1.6

    .. versionchanged:: 1.7.1

        Passwords > 16 bytes are now rejected / throw error instead of being silently truncated,
        to match Cisco behavior.  A number of :ref:`bugs <passlib-asa96-bug>` were fixed
        which caused prior releases to generate unverifiable hashes in certain cases.
    """
    #===================================================================
    # class attrs
    #===================================================================

    #--------------------
    # PasswordHash
    #--------------------
    name = "cisco_pix"

    truncate_size = 16

    # NOTE: these are the default policy for PasswordHash,
    #       but want to set them explicitly for now.
    truncate_error = True
    truncate_verify_reject = True

    #--------------------
    # GenericHandler
    #--------------------
    checksum_size = 16
    checksum_chars = uh.HASH64_CHARS

    #--------------------
    # custom
    #--------------------

    #: control flag signalling "cisco_asa" mode, set by cisco_asa class
    _is_asa = False

    #===================================================================
    # methods
    #===================================================================
    def _calc_checksum(self, secret):
        """
        This function implements the "encrypted" hash format used by Cisco
        PIX & ASA. It's behavior has been confirmed for ASA 9.6,
        but is presumed correct for PIX & other ASA releases,
        as it fits with known test vectors, and existing literature.

        While nearly the same, the PIX & ASA hashes have slight differences,
        so this function performs differently based on the _is_asa class flag.
        Noteable changes from PIX to ASA include password size limit
        increased from 16 -> 32, and other internal changes.
        """
        # select PIX vs or ASA mode
        asa = self._is_asa

        #
        # encode secret
        #
        # per ASA 8.4 documentation,
        # http://www.cisco.com/c/en/us/td/docs/security/asa/asa84/configuration/guide/asa_84_cli_config/ref_cli.html#Supported_Character_Sets,
        # it supposedly uses UTF-8 -- though some double-encoding issues have
        # been observed when trying to actually *set* a non-ascii password
        # via ASDM, and access via SSH seems to strip 8-bit chars.
        #
        if isinstance(secret, str):
            secret = secret.encode("utf-8")

        #
        # check if password too large
        #
        # Per ASA 9.6 changes listed in
        # http://www.cisco.com/c/en/us/td/docs/security/asa/roadmap/asa_new_features.html,
        # prior releases had a maximum limit of 32 characters.
        # Testing with an ASA 9.6 system bears this out --
        # setting 32-char password for a user account,
        # and logins will fail if any chars are appended.
        # (ASA 9.6 added new PBKDF2-based hash algorithm,
        #  which supports larger passwords).
        #
        # Per PIX documentation
        # http://www.cisco.com/en/US/docs/security/pix/pix50/configuration/guide/commands.html,
        # it would not allow passwords > 16 chars.
        #
        # Thus, we unconditionally throw a password size error here,
        # as nothing valid can come from a larger password.
        # NOTE: assuming PIX has same behavior, but at 16 char limit.
        #
        spoil_digest = None
        if len(secret) > self.truncate_size:
            if self.use_defaults:
                # called from hash()
                msg = "Password too long (%s allows at most %d bytes)" % \
                      (self.name, self.truncate_size)
                raise uh.exc.PasswordSizeError(self.truncate_size, msg=msg)
            else:
                # called from verify() --
                # We don't want to throw error, or return early,
                # as that would let attacker know too much.  Instead, we set a
                # flag to add some dummy data into the md5 digest, so that
                # output won't match truncated version of secret, or anything
                # else that's fixed and predictable.
                spoil_digest = secret + _DUMMY_BYTES

        #
        # append user to secret
        #
        # Policy appears to be:
        #
        # * Nothing appended for enable password (user = "")
        #
        # * ASA: If user present, but secret is >= 28 chars, nothing appended.
        #
        # * 1-2 byte users not allowed.
        #   DEVIATION: we're letting them through, and repeating their
        #   chars ala 3-char user, to simplify testing.
        #   Could issue warning in the future though.
        #
        # * 3 byte user has first char repeated, to pad to 4.
        #   (observed under ASA 9.6, assuming true elsewhere)
        #
        # * 4 byte users are used directly.
        #
        # * 5+ byte users are truncated to 4 bytes.
        #
        user = self.user
        if user:
            if isinstance(user, str):
                user = user.encode("utf-8")
            if not asa or len(secret) < 28:
                secret += repeat_string(user, 4)

        #
        # pad / truncate result to limit
        #
        # While PIX always pads to 16 bytes, ASA increases to 32 bytes IFF
        # secret+user > 16 bytes.  This makes PIX & ASA have different results
        # where secret size in range(13,16), and user is present --
        # PIX will truncate to 16, ASA will truncate to 32.
        #
        if asa and len(secret) > 16:
            pad_size = 32
        else:
            pad_size = 16
        secret = right_pad_string(secret, pad_size)

        #
        # md5 digest
        #
        if spoil_digest:
            # make sure digest won't match truncated version of secret
            secret += spoil_digest
        digest = md5(secret).digest()

        #
        # drop every 4th byte
        # NOTE: guessing this was done because it makes output exactly
        #       16 bytes, which may have been a general 'char password[]'
        #       size limit under PIX
        #
        digest = bytes(c for i, c in enumerate(digest) if (i + 1) & 3)

        #
        # encode using Hash64
        #
        return h64.encode_bytes(digest).decode("ascii")

    # NOTE: works, but needs UTs.
    # @classmethod
    # def same_as_pix(cls, secret, user=""):
    #     """
    #     test whether (secret + user) combination should
    #     have the same hash under PIX and ASA.
    #
    #     mainly present to help unittests.
    #     """
    #     # see _calc_checksum() above for details of this logic.
    #     size = len(to_bytes(secret, "utf-8"))
    #     if user and size < 28:
    #         size += 4
    #     return size < 17

    #===================================================================
    # eoc
    #===================================================================


class cisco_asa(cisco_pix):
    """
    This class implements the password hash used by Cisco ASA/PIX 7.0 and newer (2005).
    Aside from a different internal algorithm, it's use and format is identical
    to the older :class:`cisco_pix` class.

    For passwords less than 13 characters, this should be identical to :class:`!cisco_pix`,
    but will generate a different hash for most larger inputs
    (See the `Format & Algorithm`_ section for the details).

    This class only allows passwords <= 32 bytes, anything larger
    will result in a :exc:`~passlib.exc.PasswordSizeError` if passed to :meth:`~cisco_asa.hash`,
    and be silently rejected if passed to :meth:`~cisco_asa.verify`.

    .. versionadded:: 1.7

    .. versionchanged:: 1.7.1

        Passwords > 32 bytes are now rejected / throw error instead of being silently truncated,
        to match Cisco behavior.  A number of :ref:`bugs <passlib-asa96-bug>` were fixed
        which caused prior releases to generate unverifiable hashes in certain cases.
    """
    #===================================================================
    # class attrs
    #===================================================================

    #--------------------
    # PasswordHash
    #--------------------
    name = "cisco_asa"

    #--------------------
    # TruncateMixin
    #--------------------
    truncate_size = 32

    #--------------------
    # cisco_pix
    #--------------------
    _is_asa = True

    #===================================================================
    # eoc
    #===================================================================

#=============================================================================
# type 7
#=============================================================================
class cisco_type7(uh.GenericHandler):
    """
    This class implements the "Type 7" password encoding used by Cisco IOS,
    and follows the :ref:`password-hash-api`.
    It has a simple 4-5 bit salt, but is nonetheless a reversible encoding
    instead of a real hash.

    The :meth:`~passlib.ifc.PasswordHash.using` method accepts the following optional keywords:

    :type salt: int
    :param salt:
        This may be an optional salt integer drawn from ``range(0,16)``.
        If omitted, one will be chosen at random.

    :type relaxed: bool
    :param relaxed:
        By default, providing an invalid value for one of the other
        keywords will result in a :exc:`ValueError`. If ``relaxed=True``,
        and the error can be corrected, a :exc:`~passlib.exc.PasslibHashWarning`
        will be issued instead. Correctable errors include
        ``salt`` values that are out of range.

    Note that while this class outputs digests in upper-case hexadecimal,
    it will accept lower-case as well.

    This class also provides the following additional method:

    .. automethod:: decode
    """
    #===================================================================
    # class attrs
    #===================================================================

    #--------------------
    # PasswordHash
    #--------------------
    name = "cisco_type7"
    setting_kwds = ("salt",)

    #--------------------
    # GenericHandler
    #--------------------
    checksum_chars = uh.UPPER_HEX_CHARS

    #--------------------
    # HasSalt
    #--------------------

    # NOTE: encoding could handle max_salt_value=99, but since key is only 52
    #       chars in size, not sure what appropriate behavior is for that edge case.
    min_salt_value = 0
    max_salt_value = 52

    #===================================================================
    # methods
    #===================================================================
    @classmethod
    def using(cls, salt=None, **kwds):
        subcls = super().using(**kwds)
        if salt is not None:
            salt = subcls._norm_salt(salt, relaxed=kwds.get("relaxed"))
            subcls._generate_salt = staticmethod(lambda: salt)
        return subcls

    @classmethod
    def from_string(cls, hash):
        hash = to_unicode(hash, "ascii", "hash")
        if len(hash) < 2:
            raise uh.exc.InvalidHashError(cls)
        salt = int(hash[:2]) # may throw ValueError
        return cls(salt=salt, checksum=hash[2:].upper())

    def __init__(self, salt=None, **kwds):
        super().__init__(**kwds)
        if salt is not None:
            salt = self._norm_salt(salt)
        elif self.use_defaults:
            salt = self._generate_salt()
            assert self._norm_salt(salt) == salt, "generated invalid salt: %r" % (salt,)
        else:
            raise TypeError("no salt specified")
        self.salt = salt

    @classmethod
    def _norm_salt(cls, salt, relaxed=False):
        """
        validate & normalize salt value.
        .. note::
            the salt for this algorithm is an integer 0-52, not a string
        """
        if not isinstance(salt, int):
            raise uh.exc.ExpectedTypeError(salt, "integer", "salt")
        if 0 <= salt <= cls.max_salt_value:
            return salt
        msg = "salt/offset must be in 0..52 range"
        if relaxed:
            warn(msg, uh.PasslibHashWarning)
            return 0 if salt < 0 else cls.max_salt_value
        else:
            raise ValueError(msg)

    @staticmethod
    def _generate_salt():
        return uh.rng.randint(0, 15)

    def to_string(self):
        return "%02d%s" % (self.salt, self.checksum)

    def _calc_checksum(self, secret):
        # XXX: no idea what unicode policy is, but all examples are
        # 7-bit ascii compatible, so using UTF-8
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        return hexlify(self._cipher(secret, self.salt)).decode("ascii").upper()

    @classmethod
    def decode(cls, hash, encoding="utf-8"):
        """decode hash, returning original password.

        :arg hash: encoded password
        :param encoding: optional encoding to use (defaults to ``UTF-8``).
        :returns: password as unicode
        """
        self = cls.from_string(hash)
        tmp = unhexlify(self.checksum.encode("ascii"))
        raw = self._cipher(tmp, self.salt)
        return raw.decode(encoding) if encoding else raw

    # type7 uses a xor-based vingere variant, using the following secret key:
    _key = u"dsfd;kfoA,.iyewrkldJKDHSUBsgvca69834ncxv9873254k;fg87"

    @classmethod
    def _cipher(cls, data: bytes, salt: int):
        """xor static key against data - encrypts & decrypts"""
        key = cls._key
        key_size = len(key)
        return bytes(
            value ^ ord(key[(salt + idx) % key_size])
            for idx, value in enumerate(data)
        )

#=============================================================================
# eof
#=============================================================================
