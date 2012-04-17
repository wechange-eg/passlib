"""passlib.handlers.oracle - Oracle DB Password Hashes"""
#=========================================================
#imports
#=========================================================
#core
from binascii import hexlify, unhexlify
from hashlib import sha1
import re
import logging; log = logging.getLogger(__name__)
from warnings import warn
#site
#libs
#pkg
from passlib.utils import to_unicode, to_native_str, xor_bytes
from passlib.utils.compat import b, bytes, bascii_to_str, irange, u, \
                                 uascii_to_str, unicode, str_to_uascii
from passlib.utils.des import des_encrypt_block
import passlib.utils.handlers as uh
#local
__all__ = [
    "oracle10g",
    "oracle11g"
]

#=========================================================
#oracle10
#=========================================================
def des_cbc_encrypt(key, value, iv=b('\x00') * 8, pad=b('\x00')):
    """performs des-cbc encryption, returns only last block.

    this performs a specific DES-CBC encryption implementation
    as needed by the Oracle10 hash. it probably won't be useful for
    other purposes as-is.

    input value is null-padded to multiple of 8 bytes.

    :arg key: des key as bytes
    :arg value: value to encrypt, as bytes.
    :param iv: optional IV
    :param pad: optional pad byte

    :returns: last block of DES-CBC encryption of all ``value``'s byte blocks.
    """
    value += pad * (-len(value) % 8) #null pad to multiple of 8
    hash = iv #start things off
    for offset in irange(0,len(value),8):
        chunk = xor_bytes(hash, value[offset:offset+8])
        hash = des_encrypt_block(key, chunk)
    return hash

#: magic string used as initial des key by oracle10
ORACLE10_MAGIC = b("\x01\x23\x45\x67\x89\xAB\xCD\xEF")

class oracle10(uh.HasUserContext, uh.StaticHandler):
    """This class implements the password hash used by Oracle up to version 10g, and follows the :ref:`password-hash-api`.

    It has no salt and a single fixed round.

    The :meth:`encrypt()` and :meth:`genconfig` methods accept no optional keywords.

    The :meth:`encrypt()`, :meth:`genhash()`, and :meth:`verify()` methods all require the
    following additional contextual keywords:

    :param user: string containing name of oracle user account this password is associated with.
    """
    #=========================================================
    # algorithm information
    #=========================================================
    name = "oracle10"
    summary = "DES-based hash, used to store Oracle <= 10g user passwords"
    checksum_chars = uh.UPPER_HEX_CHARS
    checksum_size = 16

    #=========================================================
    # methods
    #=========================================================
    @classmethod
    def _norm_hash(cls, hash):
        return hash.upper()

    def _calc_checksum(self, secret):
        #FIXME: not sure how oracle handles unicode.
        # online docs about 10g hash indicate it puts ascii chars
        # in a 2-byte encoding w/ the high byte set to null.
        # they don't say how it handles other chars, or what encoding.
        #
        # so for now, encoding secret & user to utf-16-be, since that fits,
        # and if secret/user is bytes, we assume utf-8, and decode first.
        #
        # this whole mess really needs someone w/ an oracle system,
        # and some answers :)
        if isinstance(secret, bytes):
            secret = secret.decode("utf-8")
        user = to_unicode(self.user, "utf-8", errname="user")
        input = (user+secret).upper().encode("utf-16-be")
        hash = des_cbc_encrypt(ORACLE10_MAGIC, input)
        hash = des_cbc_encrypt(hash, input)
        return hexlify(hash).decode("ascii").upper()

    #=========================================================
    #eoc
    #=========================================================

#=========================================================
#oracle11
#=========================================================
class oracle11(uh.HasSalt, uh.GenericHandler):
    """This class implements the Oracle11g password hash, and follows the :ref:`password-hash-api`.

    It supports a fixed-length salt.

    The :meth:`encrypt()` and :meth:`genconfig` methods accept the following optional keywords:

    :param salt:
        Optional salt string.
        If not specified, one will be autogenerated (this is recommended).
        If specified, it must be 20 hexidecimal characters.
    """
    #=========================================================
    #class attrs
    #=========================================================
    #--GenericHandler--
    name = "oracle11"
    summary = "SHA1-based hash, used to store Oracle 11g+ user passwords"
    setting_kwds = ("salt",)
    checksum_size = 40
    checksum_chars = uh.UPPER_HEX_CHARS

    _stub_checksum = u('0') * 40

    #--HasSalt--
    min_salt_size = max_salt_size = 20
    salt_chars = uh.UPPER_HEX_CHARS


    #=========================================================
    #methods
    #=========================================================
    _hash_regex = re.compile(u("^S:(?P<chk>[0-9a-f]{40})(?P<salt>[0-9a-f]{20})$"), re.I)

    @classmethod
    def from_string(cls, hash):
        hash = to_unicode(hash, "ascii", "hash")
        m = cls._hash_regex.match(hash)
        if not m:
            raise uh.exc.InvalidHashError(cls)
        salt, chk = m.group("salt", "chk")
        return cls(salt=salt, checksum=chk.upper())

    def to_string(self):
        chk = (self.checksum or self._stub_checksum)
        hash = u("S:%s%s") % (chk.upper(), self.salt.upper())
        return uascii_to_str(hash)

    def _calc_checksum(self, secret):
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")
        chk = sha1(secret + unhexlify(self.salt.encode("ascii"))).hexdigest()
        return str_to_uascii(chk).upper()

    #=========================================================
    #eoc
    #=========================================================

#=========================================================
#eof
#=========================================================
