"""passlib.pbkdf2 - PBKDF2 support

this module is getting increasingly poorly named.
maybe rename to "kdf" since it's getting more key derivation functions added.
"""
#=============================================================================
# imports
#=============================================================================
from __future__ import division
# core
import logging; log = logging.getLogger(__name__)
# site
# pkg
from passlib.exc import ExpectedTypeError
from passlib.utils import deprecated_function
from passlib.utils.compat import native_string_types
from passlib.crypto.digest import norm_hash_name, lookup_hash, pbkdf1 as _pbkdf1, pbkdf2_hmac, compile_hmac
# local
__all__ = [
    # hash utils
    "norm_hash_name",

    # prf utils
    "get_prf",

    # kdfs
    "pbkdf1",
    "pbkdf2",
]

#=============================================================================
# issue deprecation warning for module
#=============================================================================
from warnings import warn

warn("the module 'passlib.utils.pbkdf2' is deprecated as of Passlib 1.7, "
     "and will be removed in Passlib 2.0, please use 'passlib.crypto' instead",
     DeprecationWarning)

#=============================================================================
# hash helpers
#=============================================================================

norm_hash_name = deprecated_function(deprecated="1.7", removed="1.8", func_module=__name__,
    replacement="passlib.crypto.digest.norm_hash_name")(norm_hash_name)

#=============================================================================
# prf lookup
#=============================================================================

#: cache mapping prf name/func -> (func, digest_size)
_prf_cache = {}

#: list of accepted prefixes
_HMAC_PREFIXES = ("hmac_", "hmac-")

def get_prf(name):
    """Lookup pseudo-random family (PRF) by name.

    :arg name:
        This must be the name of a recognized prf.
        Currently this only recognizes names with the format
        :samp:`hmac-{digest}`, where :samp:`{digest}`
        is the name of a hash function such as
        ``md5``, ``sha256``, etc.

        todo: restore text about callables.

    :raises ValueError: if the name is not known
    :raises TypeError: if the name is not a callable or string

    :returns:
        a tuple of :samp:`({prf_func}, {digest_size})`, where:

        * :samp:`{prf_func}` is a function implementing
          the specified PRF, and has the signature
          ``prf_func(secret, message) -> digest``.

        * :samp:`{digest_size}` is an integer indicating
          the number of bytes the function returns.

    Usage example::

        >>> from passlib.utils.pbkdf2 import get_prf
        >>> hmac_sha256, dsize = get_prf("hmac-sha256")
        >>> hmac_sha256
        <function hmac_sha256 at 0x1e37c80>
        >>> dsize
        32
        >>> digest = hmac_sha256('password', 'message')

    This function will attempt to return the fastest implementation
    it can find. Primarily, if M2Crypto is present, and supports the specified PRF,
    :func:`M2Crypto.EVP.hmac` will be used behind the scenes.

    .. deprecated:: 1.7

        This function is deprecated, and will be removed in Passlib 2.0.
        This only related replacement is :func:`passlib.crypto.digest.compile_hmac`.
    """
    global _prf_cache
    if name in _prf_cache:
        return _prf_cache[name]
    if isinstance(name, native_string_types):
        if not name.startswith(_HMAC_PREFIXES):
            raise ValueError("unknown prf algorithm: %r" % (name,))
        digest = lookup_hash(name[5:]).name
        def hmac(key, msg):
            return compile_hmac(digest, key)(msg)
        record = (hmac, hmac.digest_info.digest_size)
    elif callable(name):
        # assume it's a callable, use it directly
        digest_size = len(name(b'x', b'y'))
        record = (name, digest_size)
    else:
        raise ExpectedTypeError(name, "str or callable", "prf name")
    _prf_cache[name] = record
    return record

#=============================================================================
# pbkdf1 support
#=============================================================================
def pbkdf1(secret, salt, rounds, keylen=None, hash="sha1"):
    """pkcs#5 password-based key derivation v1.5

    :arg secret: passphrase to use to generate key
    :arg salt: salt string to use when generating key
    :param rounds: number of rounds to use to generate key
    :arg keylen: number of bytes to generate (if ``None``, uses digest's native size)
    :param hash:
        hash function to use. must be name of a hash recognized by hashlib.

    :returns:
        raw bytes of generated key

    .. note::

        This algorithm has been deprecated, new code should use PBKDF2.
        Among other limitations, ``keylen`` cannot be larger
        than the digest size of the specified hash.

    .. deprecated:: 1.7

        This has been relocated to :func:`passlib.crypto.digest.pbkdf1`,
        and this version will be removed in Passlib 2.0.
        *Note the call signature has changed.*
    """
    return _pbkdf1(hash, secret, salt, rounds, keylen)

#=============================================================================
# pbkdf2
#=============================================================================
def pbkdf2(secret, salt, rounds, keylen=None, prf="hmac-sha1"):
    """pkcs#5 password-based key derivation v2.0

    :arg secret:
        passphrase to use to generate key

    :arg salt:
        salt string to use when generating key

    :param rounds:
        number of rounds to use to generate key

    :arg keylen:
        number of bytes to generate.
        if set to ``None``, will use digest size of selected prf.

    :param prf:
        psuedo-random family to use for key strengthening.
        this must be a string starting with ``"hmac-"``, followed by the name of a known digest.
        this defaults to ``"hmac-sha1"`` (the only prf explicitly listed in
        the PBKDF2 specification)

        .. rst-class:: warning

        .. versionchanged 1.7:

            This argument no longer supports arbitrary PRF callables --
            These were rarely / never used, and created too many unwanted codepaths.

    :returns:
        raw bytes of generated key

    .. deprecated:: 1.7

        This has been deprecated in favor of :func:`passlib.crypto.digest.pbkdf2_hmac`,
        and will be removed in Passlib 2.0.  *Note the call signature has changed.*
    """
    if callable(prf) or (isinstance(prf, native_string_types) and not prf.startswith(_HMAC_PREFIXES)):
        raise NotImplementedError("non-HMAC prfs are not supported as of Passlib 1.7")
    digest = prf[5:]
    return pbkdf2_hmac(digest, secret, salt, rounds, keylen)

#=============================================================================
# eof
#=============================================================================
