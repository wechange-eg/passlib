"""passlib.drivers.md5_crypt - md5-crypt algorithm"""
#=========================================================
#imports
#=========================================================
#core
from hashlib import md5
import re
import logging; log = logging.getLogger(__name__)
from warnings import warn
#site
#libs
from passlib.utils import h64, autodocument, os_crypt
from passlib.utils.drivers import ExtHash, BackendExtHash
#pkg
#local
__all__ = [
    "md5_crypt",
    "apr_md5_crypt",
]

#=========================================================
#pure-python backend
#=========================================================
def raw_md5_crypt(secret, salt, apr=False):
    "perform raw md5 encryption"
    #NOTE: regarding 'apr' format:
    # really, apache? you had to invent a whole new "$apr1$" format,
    # when all you did was change the ident incorporated into the hash?
    # would love to find webpage explaining why just using a portable
    # implementation of $1$ wasn't sufficient. *nothing* else was changed.

    #validate secret
    if not isinstance(secret, str):
        raise TypeError, "secret must be string"

    #validate salt
    if len(salt) > 8:
        salt = salt[:8]

    #primary hash = secret+id+salt+...
    h = md5(secret)
    h.update("$apr1$" if apr else "$1$")
    h.update(salt)

    # primary hash - add len(secret) chars of tmp hash,
    # where temp hash is md5(secret+salt+secret)
    tmp = md5(secret + salt + secret).digest()
    assert len(tmp) == 16
    slen = len(secret)
    h.update(tmp * (slen//16) + tmp[:slen % 16])

    # primary hash - add null chars & first char of secret !?!
    #
    # this may have historically been a bug,
    # where they meant to use tmp[0] instead of '\x00',
    # but the code memclear'ed the buffer,
    # and now all implementations have to use this.
    #
    # sha-crypt replaced this step with
    # something more useful, anyways
    idx = len(secret)
    evenchar = secret[:1]
    while idx > 0:
        h.update('\x00' if idx & 1 else evenchar)
        idx >>= 1
    result = h.digest()

    #next:
    # do 1000 rounds of md5 to make things harder.
    # each round we do digest of round-specific content,
    # where content is formed from concatenation of...
    #   secret if round % 2 else result
    #   salt if round % 3
    #   secret if round % 7
    #   result if round % 2 else secret
    #
    #NOTE:
    # instead of doing this directly, this implementation
    # pre-computes all the combinations of strings & md5 hash objects
    # that will be needed, in order to perform round operations as fast as possible
    # (each round consists of one hash create/copy + 1 update + 1 digest)
    secret_secret = secret*2
    salt_secret = salt+secret
    salt_secret_secret = salt + secret*2
    secret_hash = md5(secret).copy
    secret_secret_hash = md5(secret_secret).copy
    secret_salt_hash = md5(secret+salt).copy
    secret_salt_secret_hash = md5(secret+salt_secret).copy
    for idx in xrange(1000):
        if idx & 1:
            if idx % 3:
                if idx % 7:
                    h = secret_salt_secret_hash()
                else:
                    h = secret_salt_hash()
            elif idx % 7:
                h = secret_secret_hash()
            else:
                h = secret_hash()
            h.update(result)
        else:
            h = md5(result)
            if idx % 3:
                if idx % 7:
                    h.update(salt_secret_secret)
                else:
                    h.update(salt_secret)
            elif idx % 7:
                h.update(secret_secret)
            else:
                h.update(secret)
        result = h.digest()

    #encode resulting hash
    return h64.encode_transposed_bytes(result, _chk_offsets)

_chk_offsets = (
    12,6,0,
    13,7,1,
    14,8,2,
    15,9,3,
    5,10,4,
    11,
)

#=========================================================
#handler
#=========================================================
class md5_crypt(BackendExtHash):
    #=========================================================
    #algorithm information
    #=========================================================
    name = "md5_crypt"
    #stats: 128 bit checksum, 48 bit salt

    setting_kwds = ("salt",)

    min_salt_chars = 0
    max_salt_chars = 8

    checksum_chars = 22

    #=========================================================
    #internal helpers
    #=========================================================
    @classmethod
    def identify(cls, hash):
        return bool(hash) and hash.startswith("$1$")

    _pat = re.compile(r"""
        ^
        \$1
        \$(?P<salt>[A-Za-z0-9./]{,8})
        (\$(?P<chk>[A-Za-z0-9./]{22})?)?
        $
        """, re.X)

    @classmethod
    def from_string(cls, hash):
        if not hash:
            raise ValueError, "no hash specified"
        m = cls._pat.match(hash)
        if not m:
            raise ValueError, "invalid md5-crypt hash"
        salt, chk = m.group("salt", "chk")
        return cls(salt=salt, checksum=chk, strict=bool(chk))

    def to_string(self):
        return "$1$%s$%s" % (self.salt, self.checksum or '')

    #=========================================================
    #primary interface
    #=========================================================
    backends = ("os_crypt", "builtin")

    _has_backend_builtin = True

    @classmethod
    def _has_backend_os_crypt(cls):
        return os_crypt and os_crypt("test", "$1$test") == '$1$test$pi/xDtU5WFVRqYS6BMU8X/'

    def _calc_checksum_builtin(self, secret):
        #FIXME: can't find definitive policy on how md5-crypt handles non-ascii.
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")
        return raw_md5_crypt(secret, self.salt)

    def _calc_checksum_os_crypt(self, secret):
        #FIXME: can't find definitive policy on how md5-crypt handles non-ascii.
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")
        return os_crypt(secret, self.to_string())[-22:]

    #=========================================================
    #eoc
    #=========================================================

autodocument(md5_crypt)

#=========================================================
#apache variant of md5-crypt
#=========================================================
class apr_md5_crypt(ExtHash):
    #=========================================================
    #algorithm information
    #=========================================================
    name = "apr_md5_crypt"
    setting_kwds = ("salt",)

    min_salt_chars = 0
    max_salt_chars = 8

    checksum_chars = 22

    #=========================================================
    #internal helpers
    #=========================================================
    _pat = re.compile(r"""
        ^
        \$apr1
        \$(?P<salt>[A-Za-z0-9./]{,8})
        (\$(?P<chk>[A-Za-z0-9./]{22})?)?
        $
        """, re.X)

    @classmethod
    def identify(cls, hash):
        return bool(hash) and hash.startswith("$apr1$")

    @classmethod
    def from_string(cls, hash):
        if not hash:
            raise ValueError, "no hash specified"
        m = cls._pat.match(hash)
        if not m:
            raise ValueError, "invalid md5-crypt hash"
        salt, chk = m.group("salt", "chk")
        return cls(salt=salt, checksum=chk, strict=bool(chk))

    def to_string(self):
        return "$apr1$%s$%s" % (self.salt, self.checksum or '')

    #=========================================================
    #primary interface
    #=========================================================
    def calc_checksum(self, secret):
        #FIXME: can't find definitive policy on how md5-crypt handles non-ascii.
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")
        return raw_md5_crypt(secret, self.salt, apr=True)

    #=========================================================
    #eoc
    #=========================================================

autodocument(apr_md5_crypt)
#=========================================================
#eof
#=========================================================
