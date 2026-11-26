"""Monta links do Google Flights (parâmetro tfs = protobuf em base64)."""
import base64


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _campo(num: int, tipo: int, valor: bytes) -> bytes:
    return _varint(num << 3 | tipo) + valor


def _str(num: int, s: str) -> bytes:
    b = s.encode()
    return _campo(num, 2, _varint(len(b)) + b)


def _msg(num: int, corpo: bytes) -> bytes:
    return _campo(num, 2, _varint(len(corpo)) + corpo)


def _int(num: int, v: int) -> bytes:
    return _campo(num, 0, _varint(v))


def _trecho(data: str, origem: str, destino: str, max_paradas: int | None) -> bytes:
    corpo = _str(2, data)
    if max_paradas is not None:
        corpo += _int(5, max_paradas)
    corpo += _msg(13, _str(2, origem)) + _msg(14, _str(2, destino))
    return corpo


def tfs(ida: str, volta: str, origem="GRU", destino="BKK", adultos=2, max_paradas: int | None = 1) -> str:
    corpo = _msg(3, _trecho(ida, origem, destino, max_paradas))
    corpo += _msg(3, _trecho(volta, destino, origem, max_paradas))
    corpo += _msg(8, b"".join(_varint(1) for _ in range(adultos)))  # passageiros: ADULT=1 (packed)
    corpo += _int(9, 1)    # assento: ECONOMY
    corpo += _int(19, 1)   # viagem: ROUND_TRIP
    return base64.urlsafe_b64encode(corpo).decode().rstrip("=")


def url(ida: str, volta: str, **kw) -> str:
    return f"https://www.google.com/travel/flights/search?tfs={tfs(ida, volta, **kw)}&hl=pt-BR&gl=BR&curr=BRL"


if __name__ == "__main__":
    print(url("2027-03-19", "2027-04-04"))
