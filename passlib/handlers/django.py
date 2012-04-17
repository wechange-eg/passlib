"""passlib.handlers.django- Django password hash support"""
#=========================================================
#imports
#=========================================================
#core
from hashlib import md5, sha1
import re
import logging; log = logging.getLogger(__name__)
from warnings import warn
#site
#libs
from passlib.utils import to_unicode
from passlib.utils.compat import b, bytes, str_to_uascii, uascii_to_str, unicode, u
import passlib.utils.handlers as uh
#pkg
#local
__all__ = [
    "django_salted_sha1",
    "django_salted_md5",
    "django_des_crypt",
    "django_disabled",
]

#=========================================================
# lazy imports
#=========================================================
des_crypt = None

def _import_des_crypt():
    global des_crypt
    if des_crypt is None:
        from passlib.hash import des_crypt
    return des_crypt

#=========================================================
#salted hashes
#=========================================================
class DjangoSaltedHash(uh.HasSalt, uh.GenericHandler):
    """base class providing common code for django hashes"""
    #must be specified by subclass - along w/ calc_checksum
    setting_kwds = ("salt", "salt_size")
    ident = None #must have "$" suffix

    #common to most subclasses
    min_salt_size = 0
    default_salt_size = 5
    max_salt_size = None
    salt_chars = checksum_chars = uh.LOWER_HEX_CHARS

    @classmethod
    def from_string(cls, hash):
        hash = to_unicode(hash, "ascii", "hash")
        ident = cls.ident
        assert ident.endswith(u("$"))
        if not hash.startswith(ident):
            raise uh.exc.InvalidHashError(cls)
        _, salt, chk = hash.split(u("$"))
        return cls(salt=salt, checksum=chk or None)

    def to_string(self):
        chk = self.checksum or self._stub_checksum
        hash = u("%s%s$%s") % (self.ident, self.salt, chk)
        return uascii_to_str(hash)

class django_salted_sha1(DjangoSaltedHash):
    """This class implements Django's Salted SHA1 hash, and follows the :ref:`password-hash-api`.

    It supports a variable-length salt, and uses a single round of SHA1.

    The :meth:`encrypt()` and :meth:`genconfig` methods accept the following optional keywords:

    :param salt:
        Optional salt string.
        If not specified, a 5 character one will be autogenerated (this is recommended).
        If specified, may be any series of characters drawn from the regexp range ``[0-9a-f]``.

    :param salt_size:
        Optional number of characters to use when autogenerating new salts.
        Defaults to 5, but can be any non-negative value.
    """
    name = "django_salted_sha1"
    summary = "Django-specific salted SHA1 hash"
    ident = u("sha1$")
    checksum_size = 40
    _stub_checksum = u('0') * 40

    def _calc_checksum(self, secret):
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")
        return str_to_uascii(sha1(self.salt.encode("ascii") + secret).hexdigest())

class django_salted_md5(DjangoSaltedHash):
    """This class implements Django's Salted MD5 hash, and follows the :ref:`password-hash-api`.

    It supports a variable-length salt, and uses a single round of MD5.

    The :meth:`encrypt()` and :meth:`genconfig` methods accept the following optional keywords:

    :param salt:
        Optional salt string.
        If not specified, a 5 character one will be autogenerated (this is recommended).
        If specified, may be any series of characters drawn from the regexp range ``[0-9a-f]``.

    :param salt_size:
        Optional number of characters to use when autogenerating new salts.
        Defaults to 5, but can be any non-negative value.
    """
    name = "django_salted_md5"
    summary = "Django-specific salted MD5 hash"
    ident = u("md5$")
    checksum_size = 32
    _stub_checksum = u('0') * 32

    def _calc_checksum(self, secret):
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")
        return str_to_uascii(md5(self.salt.encode("ascii") + secret).hexdigest())

#=========================================================
#other
#=========================================================

class django_des_crypt(DjangoSaltedHash):
    """This class implements Django's :class:`des_crypt` wrapper, and follows the :ref:`password-hash-api`.

    It supports a fixed-length salt.

    The :meth:`encrypt()` and :meth:`genconfig` methods accept the following optional keywords:

    :param salt:
        Optional salt string.
        If not specified, one will be autogenerated (this is recommended).
        If specified, it must be 2 characters, drawn from the regexp range ``[./0-9A-Za-z]``.

    .. note::

        Django only supports this on Unix systems,
        but it is available cross-platform under Passlib.
    """

    name = "django_des_crypt"
    summary = "Django-specific DES-Crypt wrapper"
    ident = u("crypt$")
    checksum_chars = salt_chars = uh.HASH64_CHARS
    checksum_size = 13
    min_salt_size = 2

    # NOTE: checksum is full des_crypt hash,
    #       including salt as first two digits.
    #       these should always match first two digits
    #       of django_des_crypt's salt...
    #       and all remaining chars of salt are ignored.

    def __init__(self, **kwds):
        super(django_des_crypt, self).__init__(**kwds)

        # make sure salt embedded in checksum is a match,
        # else hash can *never* validate
        salt = self.salt
        chk = self.checksum
        if salt and chk:
            if salt[:2] != chk[:2]:
                raise uh.exc.MalformedHashError(self,
                    "first two digits of salt and checksum must match")
            # repeat stub checksum detection since salt isn't set
            # when _norm_checksum() is called.
            if chk == self._stub_checksum:
                self.checksum = None

    _base_stub_checksum = u('.') * 13

    @property
    def _stub_checksum(self):
        "generate stub checksum dynamically, so it matches always matches salt"
        stub = self._base_stub_checksum
        if self.salt:
            return self.salt[:2] + stub[2:]
        else:
            return stub

    def _calc_checksum(self, secret):
        # NOTE: we lazily import des_crypt,
        #       since most django deploys won't use django_des_crypt
        global des_crypt
        if des_crypt is None:
            _import_des_crypt()
        salt = self.salt[:2]
        return salt + des_crypt(salt=salt)._calc_checksum(secret)

class django_disabled(uh.StaticHandler):
    """This class provides disabled password behavior for Django, and follows the :ref:`password-hash-api`.

    This class does not implement a hash, but instead
    claims the special hash string ``"!"`` which Django uses
    to indicate an account's password has been disabled.

    * newly encrypted passwords will hash to ``!``.
    * it rejects all passwords.
    """
    name = "django_disabled"
    summary = "Django-specific placeholder for disabled accounts"

    @classmethod
    def identify(cls, hash):
        hash = uh.to_unicode_for_identify(hash)
        return hash == u("!")

    def _calc_checksum(self, secret):
        return u("!")

    @classmethod
    def verify(cls, secret, hash):
        uh.validate_secret(secret)
        if not cls.identify(hash):
            raise uh.exc.InvalidHashError(cls)
        return False

#=========================================================
#eof
#=========================================================
