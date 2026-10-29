def earned(total: int) -> int:
    return total * 5 // 100


def available(balance: int, after_discount: int) -> int:
    return min(balance, after_discount * 30 // 100)
