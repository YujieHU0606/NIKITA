"""
NIST SP 800-22 Rev. 1a - all 15 statistical test categories.

Direct-use version for PyCharm:
1. Put this .py file in the same folder as the two input files named below.
2. Each input file must be a text file containing one 0 or 1 per line.
3. Open this file in PyCharm and click Run.

The program creates a folder named ``nist_results`` containing:
- nist_summary.csv                 (one row per test category)
- nist_detailed_results.csv        (every P-value, including subtests)
- SPAD_RAW_nist_report.txt
- SPAD_VN_nist_report.txt

Notes:
- Significance level alpha = 0.01, as used by NIST SP 800-22.
- NIST's default parameters are used where applicable:
  M=128, m=9, m=9, m=10, m=16, M=500.
- Tests that cannot validly run on a short sequence are marked NOT_APPLICABLE;
  the program never invents a P-value.
- This script evaluates exactly the first 1,000,000 bits of each input file as
  one sequence, so the RAW and VN results can be compared fairly.
"""

from __future__ import annotations

import csv
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


-

INPUT_FILENAMES = (
    "SPAD_RAW_20260904_212033.txt",
    "SPAD_HASH_FPGA_20260904_212033.txt",
)
TEST_BIT_COUNT = 1_000_000
ALPHA = 0.01
AUTO_INSTALL_DEPENDENCIES = True
OUTPUT_FOLDER_NAME = "nist_results"

BLOCK_FREQUENCY_M = 128
NON_OVERLAPPING_M = 9
OVERLAPPING_M = 9
APPROXIMATE_ENTROPY_M = 10
SERIAL_M = 16
LINEAR_COMPLEXITY_M = 500


def _load_dependencies():
    """Import NumPy/SciPy; install them once if PyCharm's interpreter lacks them."""
    try:
        import numpy as numpy_module
        from scipy import special as special_module
        from scipy.stats import norm as norm_module
        return numpy_module, special_module, norm_module
    except ImportError as first_error:
        if not AUTO_INSTALL_DEPENDENCIES:
            raise RuntimeError(
                "NumPy and SciPy are required. Run: pip install numpy scipy"
            ) from first_error

        print("NumPy/SciPy are missing. Installing them into this Python interpreter...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "numpy", "scipy"]
            )
        except Exception as install_error:
            raise RuntimeError(
                "Automatic installation failed. In PyCharm Terminal run:\n"
                "pip install numpy scipy"
            ) from install_error

        import numpy as numpy_module
        from scipy import special as special_module
        from scipy.stats import norm as norm_module
        return numpy_module, special_module, norm_module


np, special, normal_distribution = _load_dependencies()


@dataclass
class TestResult:
    number: int
    name: str
    status: str
    p_values: dict[str, float]
    details: str = ""
    elapsed_seconds: float = 0.0
    summary_p_value_override: float | None = None

    @property
    def summary_p_value(self) -> float | None:
        if self.summary_p_value_override is not None:
            return self.summary_p_value_override
        if not self.p_values:
            return None
        return min(self.p_values.values())


def result_from_p_values(
    number: int,
    name: str,
    p_values: dict[str, float],
    details: str = "",
) -> TestResult:
    clean_values = {
        key: min(1.0, max(0.0, float(value)))
        for key, value in p_values.items()
        if math.isfinite(float(value))
    }
    if len(clean_values) != len(p_values):
        return TestResult(number, name, "ERROR", clean_values,
                          "A non-finite P-value was produced. " + details)
    status = "PASS" if all(p >= ALPHA for p in clean_values.values()) else "FAIL"
    return TestResult(number, name, status, clean_values, details)


def not_applicable(number: int, name: str, reason: str) -> TestResult:
    return TestResult(number, name, "NOT_APPLICABLE", {}, reason)


def read_text_bitstream(path: Path) -> np.ndarray:
    """Read a text file whose non-whitespace characters are only 0 and 1."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{path.name} is not a text bitstream. It must contain one 0/1 per line."
        ) from exc

    compact = "".join(text.split())
    if not compact:
        raise ValueError(f"{path.name} is empty.")

    invalid = sorted(set(compact) - {"0", "1"})
    if invalid:
        shown = " ".join(repr(ch) for ch in invalid[:10])
        raise ValueError(
            f"{path.name} contains characters other than 0/1: {shown}. "
            "The required format is one 0 or 1 per line."
        )

    raw = np.frombuffer(compact.encode("ascii"), dtype=np.uint8)
    return (raw - ord("0")).astype(np.uint8, copy=False)


def _pattern_counts_circular(bits: np.ndarray, m: int) -> np.ndarray:
    n = int(bits.size)
    extended = np.concatenate((bits, bits[:m - 1])) if m > 1 else bits
    codes = np.zeros(n, dtype=np.uint32)
    for offset in range(m):
        codes = (codes << 1) | extended[offset:offset + n]
    return np.bincount(codes.astype(np.int64), minlength=1 << m)


def _rolling_codes(bits: np.ndarray, m: int) -> np.ndarray:
    length = int(bits.size) - m + 1
    if length <= 0:
        return np.empty(0, dtype=np.uint16)
    codes = np.zeros(length, dtype=np.uint16)
    for offset in range(m):
        codes = (codes << 1) | bits[offset:offset + length]
    return codes


def _longest_one_run(block: np.ndarray) -> int:
    padded = np.concatenate((np.array([0], dtype=np.int8), block,
                             np.array([0], dtype=np.int8)))
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    if starts.size == 0:
        return 0
    ends = np.flatnonzero(changes == -1)
    return int(np.max(ends - starts))


def _gf2_rank(rows: list[int], width: int = 32) -> int:
    rows = rows.copy()
    rank = 0
    for column in range(width - 1, -1, -1):
        pivot = next((r for r in range(rank, len(rows))
                      if (rows[r] >> column) & 1), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        pivot_value = rows[rank]
        for r in range(len(rows)):
            if r != rank and ((rows[r] >> column) & 1):
                rows[r] ^= pivot_value
        rank += 1
        if rank == width:
            break
    return rank


def _run_lengths_of_ones(block: np.ndarray) -> np.ndarray:
    padded = np.concatenate((np.array([0], dtype=np.int8), block,
                             np.array([0], dtype=np.int8)))
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    return ends - starts


# The official 148 aperiodic 9-bit templates from NIST STS 2.1.2 template9.
_TEMPLATE9_PACKED = (
    "000000001000000011000000101000000111000001001000001011000001101000001111"
    "000010001000010011000010101000010111000011001000011011000011101000011111"
    "000100011000100101000100111000101001000101011000101101000101111000110011"
    "000110101000110111000111001000111011000111101000111111001000011001000101"
    "001000111001001011001001101001001111001010011001010101001010111001011011"
    "001011101001011111001100101001100111001101011001101101001101111001110101"
    "001110111001111011001111101001111111010000011010000111010001011010001111"
    "010010011010010111010011011010011111010100011010100111010101011010101111"
    "010110011010110111010111011010111111011000111011001111011010111011011111"
    "011101111011111111100000000100010000100100000100101000100110000100111000"
    "101000000101000100101001000101001100101010000101010100101011000101011100"
    "101100000101100100101101000101101100101110000101110100101111000101111100"
    "110000000110000010110000100110001000110001010110010000110010010110010100"
    "110011000110011010110100000110100010110100100110101000110101010110101100"
    "110110000110110010110110100110111000110111010110111100111000000111000010"
    "111000100111000110111001000111001010111001100111010000111010010111010100"
    "111010110111011000111011010111011100111100000111100010111100100111100110"
    "111101000111101010111101100111101110111110000111110010111110100111110110"
    "111111000111111010111111100111111110"
)
TEMPLATE9 = tuple(_TEMPLATE9_PACKED[i:i + 9]
                  for i in range(0, len(_TEMPLATE9_PACKED), 9))
if len(TEMPLATE9) != 148 or any(len(template) != 9 for template in TEMPLATE9):
    raise RuntimeError("Internal NIST template9 table is damaged.")



# The 15 NIST SP 800-22 tests


def test_01_frequency(bits: np.ndarray) -> TestResult:
    name = "Frequency (Monobit)"
    n = int(bits.size)
    if n < 100:
        return not_applicable(1, name, "At least 100 bits are required.")
    total = int(np.sum(bits, dtype=np.int64)) * 2 - n
    p = special.erfc(abs(total) / math.sqrt(2.0 * n))
    return result_from_p_values(1, name, {"main": p},
                                f"n={n}; ones={int(bits.sum())}; zeros={n-int(bits.sum())}")


def test_02_block_frequency(bits: np.ndarray) -> TestResult:
    name = "Frequency within a Block"
    n = int(bits.size)
    m = BLOCK_FREQUENCY_M
    blocks_number = n // m
    if blocks_number < 1:
        return not_applicable(2, name, f"At least {m} bits are required.")
    blocks = bits[:blocks_number * m].reshape(blocks_number, m)
    proportions = blocks.mean(axis=1)
    chi_square = 4.0 * m * float(np.sum((proportions - 0.5) ** 2))
    p = special.gammaincc(blocks_number / 2.0, chi_square / 2.0)
    return result_from_p_values(2, name, {"main": p},
                                f"M={m}; N={blocks_number}; discarded={n % m}; chi2={chi_square:.6g}")


def test_03_runs(bits: np.ndarray) -> TestResult:
    name = "Runs"
    n = int(bits.size)
    if n < 100:
        return not_applicable(3, name, "At least 100 bits are required.")
    pi = float(bits.mean())
    if abs(pi - 0.5) > 2.0 / math.sqrt(n):
        return TestResult(3, name, "FAIL", {"main": 0.0},
                          f"Frequency prerequisite failed; pi={pi:.8f}.")
    runs = 1 + int(np.count_nonzero(bits[1:] != bits[:-1]))
    numerator = abs(runs - 2.0 * n * pi * (1.0 - pi))
    denominator = 2.0 * math.sqrt(2.0 * n) * pi * (1.0 - pi)
    p = special.erfc(numerator / denominator)
    return result_from_p_values(3, name, {"main": p},
                                f"pi={pi:.8f}; runs={runs}")


def test_04_longest_run(bits: np.ndarray) -> TestResult:
    name = "Longest Run of Ones in a Block"
    n = int(bits.size)
    if n < 128:
        return not_applicable(4, name, "At least 128 bits are required.")
    if n < 6272:
        m, k = 8, 3
        thresholds = [1, 2, 3, 4]
        probabilities = np.array([0.21484375, 0.3671875, 0.23046875, 0.1875])
    elif n < 750000:
        m, k = 128, 5
        thresholds = [4, 5, 6, 7, 8, 9]
        probabilities = np.array([
            0.1174035788, 0.242955959, 0.249363483,
            0.17517706, 0.102701071, 0.112398847,
        ])
    else:
        m, k = 10000, 6
        thresholds = [10, 11, 12, 13, 14, 15, 16]
        probabilities = np.array([0.0882, 0.2092, 0.2483, 0.1933,
                                  0.1208, 0.0675, 0.0727])
    blocks_number = n // m
    frequencies = np.zeros(k + 1, dtype=np.int64)
    for block in bits[:blocks_number * m].reshape(blocks_number, m):
        longest = _longest_one_run(block)
        if longest <= thresholds[0]:
            index = 0
        elif longest >= thresholds[-1]:
            index = k
        else:
            index = longest - thresholds[0]
        frequencies[index] += 1
    expected = blocks_number * probabilities
    chi_square = float(np.sum((frequencies - expected) ** 2 / expected))
    p = special.gammaincc(k / 2.0, chi_square / 2.0)
    return result_from_p_values(4, name, {"main": p},
                                f"M={m}; N={blocks_number}; bins={frequencies.tolist()}; chi2={chi_square:.6g}")


def _rank_probability(rank: int, rows: int = 32, columns: int = 32) -> float:
    product = 1.0
    for i in range(rank):
        product *= ((1.0 - 2.0 ** (i - rows)) *
                    (1.0 - 2.0 ** (i - columns)) /
                    (1.0 - 2.0 ** (i - rank)))
    return 2.0 ** (rank * (rows + columns - rank) - rows * columns) * product


def test_05_binary_matrix_rank(bits: np.ndarray) -> TestResult:
    name = "Binary Matrix Rank"
    n = int(bits.size)
    matrix_bits = 32 * 32
    blocks_number = n // matrix_bits
    if blocks_number < 38:
        return not_applicable(5, name,
                              f"At least 38 matrices ({38 * matrix_bits} bits) are required; found {blocks_number}.")
    matrices = bits[:blocks_number * matrix_bits].reshape(blocks_number, 32, 32)
    weights = (1 << np.arange(31, -1, -1, dtype=np.uint64))
    row_values = np.sum(matrices.astype(np.uint64) * weights, axis=2, dtype=np.uint64)
    ranks = np.array([_gf2_rank([int(value) for value in matrix_rows])
                      for matrix_rows in row_values], dtype=np.int16)
    observed = np.array([np.count_nonzero(ranks == 32),
                         np.count_nonzero(ranks == 31),
                         np.count_nonzero(ranks <= 30)], dtype=float)
    p32 = _rank_probability(32)
    p31 = _rank_probability(31)
    probabilities = np.array([p32, p31, 1.0 - p32 - p31])
    expected = blocks_number * probabilities
    chi_square = float(np.sum((observed - expected) ** 2 / expected))
    p = math.exp(-chi_square / 2.0)
    return result_from_p_values(5, name, {"main": p},
                                f"matrices={blocks_number}; ranks[32,31,<=30]={observed.astype(int).tolist()}; chi2={chi_square:.6g}")


def test_06_dft(bits: np.ndarray) -> TestResult:
    name = "Discrete Fourier Transform (Spectral)"
    n = int(bits.size)
    if n < 1000:
        return not_applicable(6, name, "At least 1,000 bits are required.")
    sequence = bits.astype(np.float64) * 2.0 - 1.0
    magnitudes = np.abs(np.fft.rfft(sequence))[:n // 2]
    threshold = math.sqrt(2.995732274 * n)
    observed_below = int(np.count_nonzero(magnitudes < threshold))
    expected_below = 0.95 * n / 2.0
    d = ((observed_below - expected_below) /
         math.sqrt(n / 4.0 * 0.95 * 0.05))
    p = special.erfc(abs(d) / math.sqrt(2.0))
    warning = " NIST recommends 1,000,000 bits for this test." if n < 1_000_000 else ""
    return result_from_p_values(6, name, {"main": p},
                                f"peaks_below={observed_below}; expected={expected_below:.3f}; d={d:.6g}.{warning}")


def _count_non_overlapping_positions(positions: np.ndarray, m: int) -> int:
    count = 0
    next_allowed = 0
    for position_value in positions:
        position = int(position_value)
        if position >= next_allowed:
            count += 1
            next_allowed = position + m
    return count


def test_07_non_overlapping_template(bits: np.ndarray) -> TestResult:
    name = "Non-overlapping Template Matching"
    n = int(bits.size)
    m = NON_OVERLAPPING_M
    blocks_number = 8
    block_length = n // blocks_number
    if m != 9:
        return not_applicable(7, name, "This direct-use script includes the official m=9 template library only.")
    if block_length < 100:
        return not_applicable(7, name, "The sequence is too short for eight meaningful template blocks.")
    used = bits[:blocks_number * block_length].reshape(blocks_number, block_length)
    block_codes = [_rolling_codes(block, m) for block in used]
    mean = (block_length - m + 1) / (2.0 ** m)
    variance = block_length * (1.0 / (2.0 ** m) -
                               (2.0 * m - 1.0) / (2.0 ** (2 * m)))
    p_values: dict[str, float] = {}
    for template in TEMPLATE9:
        template_code = int(template, 2)
        counts = np.array([
            _count_non_overlapping_positions(np.flatnonzero(codes == template_code), m)
            for codes in block_codes
        ], dtype=float)
        chi_square = float(np.sum((counts - mean) ** 2 / variance))
        p_values[f"template_{template}"] = float(
            special.gammaincc(blocks_number / 2.0, chi_square / 2.0)
        )
    failed = sum(p < ALPHA for p in p_values.values())
    table_template = "000000001"
    table_p_value = p_values[f"template_{table_template}"]
    details = (f"Table result uses B={table_template}; all 148 templates are retained "
               f"in the detailed CSV. m={m}; blocks=8; M={block_length}; "
               f"all-template subtests_failed={failed}; "
               f"discarded={n - blocks_number * block_length}")
    result = result_from_p_values(7, name, p_values, details)
    result.summary_p_value_override = table_p_value
    result.status = "PASS" if table_p_value >= ALPHA else "FAIL"
    return result


def _overlap_probability(u: int, eta: float) -> float:
    if u == 0:
        return math.exp(-eta)
    total = 0.0
    for ell in range(1, u + 1):
        log_term = (-eta - u * math.log(2.0) + ell * math.log(eta)
                    - special.gammaln(ell + 1) + special.gammaln(u)
                    - special.gammaln(ell) - special.gammaln(u - ell + 1))
        total += math.exp(log_term)
    return total


def test_08_overlapping_template(bits: np.ndarray) -> TestResult:
    name = "Overlapping Template Matching"
    n = int(bits.size)
    m = OVERLAPPING_M
    block_length = 1032
    blocks_number = n // block_length
    if blocks_number < 1:
        return not_applicable(8, name, f"At least {block_length} bits are required.")
    used = bits[:blocks_number * block_length].reshape(blocks_number, block_length)
    observed_bins = np.zeros(6, dtype=np.int64)
    for block in used:
        run_lengths = _run_lengths_of_ones(block)
        occurrences = int(np.sum(np.maximum(run_lengths - m + 1, 0)))
        observed_bins[min(occurrences, 5)] += 1
    lam = (block_length - m + 1) / (2.0 ** m)
    eta = lam / 2.0
    probabilities = np.array([_overlap_probability(u, eta) for u in range(5)])
    probabilities = np.append(probabilities, 1.0 - probabilities.sum())
    expected = blocks_number * probabilities
    chi_square = float(np.sum((observed_bins - expected) ** 2 / expected))
    p = special.gammaincc(5.0 / 2.0, chi_square / 2.0)
    return result_from_p_values(8, name, {"main": p},
                                f"m={m}; M=1032; N={blocks_number}; bins={observed_bins.tolist()}; chi2={chi_square:.6g}")


def test_09_maurer_universal(bits: np.ndarray) -> TestResult:
    name = "Maurer’s Universal Statistical"
    n = int(bits.size)
    thresholds = [
        (1_059_061_760, 16), (496_435_200, 15), (231_669_760, 14),
        (107_560_960, 13), (49_643_520, 12), (22_753_280, 11),
        (10_342_400, 10), (4_654_080, 9), (2_068_480, 8),
        (904_960, 7), (387_840, 6),
    ]
    block_size = next((l for threshold, l in thresholds if n >= threshold), None)
    if block_size is None:
        return not_applicable(9, name, "At least 387,840 bits are required.")
    expected_value = {
        6: 5.2177052, 7: 6.1962507, 8: 7.1836656, 9: 8.1764248,
        10: 9.1723243, 11: 10.170032, 12: 11.168765,
        13: 12.168070, 14: 13.167693, 15: 14.167488, 16: 15.167379,
    }
    variance = {
        6: 2.954, 7: 3.125, 8: 3.238, 9: 3.311, 10: 3.356,
        11: 3.384, 12: 3.401, 13: 3.410, 14: 3.416, 15: 3.419, 16: 3.421,
    }
    l = block_size
    q = 10 * (1 << l)
    total_blocks = n // l
    k = total_blocks - q
    blocks = bits[:total_blocks * l].reshape(total_blocks, l)
    weights = (1 << np.arange(l - 1, -1, -1, dtype=np.uint64))
    codes = np.sum(blocks.astype(np.uint64) * weights, axis=1, dtype=np.uint64)
    table = np.zeros(1 << l, dtype=np.int64)
    for index in range(q):
        table[int(codes[index])] = index + 1
    log_sum = 0.0
    for index in range(q, total_blocks):
        code = int(codes[index])
        position = index + 1
        log_sum += math.log2(position - int(table[code]))
        table[code] = position
    phi = log_sum / k
    correction = (0.7 - 0.8 / l +
                  (4.0 + 32.0 / l) * (k ** (-3.0 / l)) / 15.0)
    sigma = correction * math.sqrt(variance[l] / k)
    p = special.erfc(abs(phi - expected_value[l]) / (math.sqrt(2.0) * sigma))
    return result_from_p_values(9, name, {"main": p},
                                f"L={l}; Q={q}; K={k}; phi={phi:.8f}; expected={expected_value[l]:.8f}")


def _berlekamp_massey_length(block: np.ndarray) -> int:
    """Binary Berlekamp-Massey using Python integers as polynomial bitsets."""
    connection = 1
    previous = 1
    complexity = 0
    last_update = -1
    history = 0
    for index, bit_value in enumerate(block):
        history = (history << 1) | int(bit_value)
        discrepancy = (connection & history).bit_count() & 1
        if discrepancy:
            old_connection = connection
            connection ^= previous << (index - last_update)
            if complexity <= index // 2:
                complexity = index + 1 - complexity
                last_update = index
                previous = old_connection
    return complexity


def test_10_linear_complexity(bits: np.ndarray) -> TestResult:
    name = "Linear Complexity"
    n = int(bits.size)
    m = LINEAR_COMPLEXITY_M
    blocks_number = n // m
    if blocks_number < 10:
        return not_applicable(10, name, f"At least {10 * m} bits are required.")
    probabilities = np.array([0.01047, 0.03125, 0.12500, 0.50000,
                              0.25000, 0.06250, 0.020833])
    frequencies = np.zeros(7, dtype=np.int64)
    parity_sign = -1 if (m + 1) % 2 == 0 else 1
    mean = (m / 2.0 + (9.0 + parity_sign) / 36.0 -
            (m / 3.0 + 2.0 / 9.0) / (2.0 ** m))
    t_sign = 1 if m % 2 == 0 else -1
    for block in bits[:blocks_number * m].reshape(blocks_number, m):
        complexity = _berlekamp_massey_length(block)
        transformed = t_sign * (complexity - mean) + 2.0 / 9.0
        if transformed <= -2.5:
            category = 0
        elif transformed <= -1.5:
            category = 1
        elif transformed <= -0.5:
            category = 2
        elif transformed <= 0.5:
            category = 3
        elif transformed <= 1.5:
            category = 4
        elif transformed <= 2.5:
            category = 5
        else:
            category = 6
        frequencies[category] += 1
    expected = blocks_number * probabilities
    chi_square = float(np.sum((frequencies - expected) ** 2 / expected))
    p = special.gammaincc(3.0, chi_square / 2.0)
    warning = " NIST recommends n >= 1,000,000 bits." if n < 1_000_000 else ""
    return result_from_p_values(10, name, {"main": p},
                                f"M={m}; N={blocks_number}; bins={frequencies.tolist()}; chi2={chi_square:.6g}.{warning}")


def _serial_psi(bits: np.ndarray, m: int) -> float:
    if m <= 0:
        return 0.0
    counts = _pattern_counts_circular(bits, m).astype(np.float64)
    n = float(bits.size)
    return float((2.0 ** m / n) * np.sum(counts * counts) - n)


def test_11_serial(bits: np.ndarray) -> TestResult:
    name = "Serial"
    n = int(bits.size)
    if n < 128:
        return not_applicable(11, name, "At least 128 bits are required.")
    recommended_max = max(2, int(math.floor(math.log2(n))) - 2)
    m = min(SERIAL_M, recommended_max)
    psi_m = _serial_psi(bits, m)
    psi_m1 = _serial_psi(bits, m - 1)
    psi_m2 = _serial_psi(bits, m - 2)
    delta1 = psi_m - psi_m1
    delta2 = psi_m - 2.0 * psi_m1 + psi_m2
    p1 = special.gammaincc(2.0 ** (m - 2), delta1 / 2.0)
    p2 = special.gammaincc(2.0 ** (m - 3), delta2 / 2.0)
    parameter_note = "default" if m == SERIAL_M else f"reduced from 16 because n={n}"
    return result_from_p_values(11, name, {"delta1": p1, "delta2": p2},
                                f"m={m} ({parameter_note}); delta1={delta1:.6g}; delta2={delta2:.6g}")


def _approximate_entropy_phi(bits: np.ndarray, m: int) -> float:
    counts = _pattern_counts_circular(bits, m).astype(np.float64)
    nonzero = counts[counts > 0]
    probabilities = nonzero / bits.size
    return float(np.sum(probabilities * np.log(probabilities)))


def test_12_approximate_entropy(bits: np.ndarray) -> TestResult:
    name = "Approximate Entropy"
    n = int(bits.size)
    if n < 128:
        return not_applicable(12, name, "At least 128 bits are required.")
    recommended_max = max(1, int(math.floor(math.log2(n))) - 5)
    m = min(APPROXIMATE_ENTROPY_M, recommended_max)
    phi_m = _approximate_entropy_phi(bits, m)
    phi_m1 = _approximate_entropy_phi(bits, m + 1)
    approximate_entropy = phi_m - phi_m1
    chi_square = 2.0 * n * (math.log(2.0) - approximate_entropy)
    p = special.gammaincc(2.0 ** (m - 1), chi_square / 2.0)
    parameter_note = "default" if m == APPROXIMATE_ENTROPY_M else f"reduced from 10 because n={n}"
    return result_from_p_values(12, name, {"main": p},
                                f"m={m} ({parameter_note}); ApEn={approximate_entropy:.8f}; chi2={chi_square:.6g}")


def _cusum_p_value(sequence: np.ndarray) -> tuple[float, int]:
    n = int(sequence.size)
    walk = np.cumsum(sequence.astype(np.int64) * 2 - 1)
    z = int(np.max(np.abs(walk)))
    sqrt_n = math.sqrt(n)
    first = 0.0
    start = math.floor((-n / z + 1.0) / 4.0)
    stop = math.floor((n / z - 1.0) / 4.0)
    for k in range(start, stop + 1):
        first += (normal_distribution.cdf((4 * k + 1) * z / sqrt_n) -
                  normal_distribution.cdf((4 * k - 1) * z / sqrt_n))
    second = 0.0
    start = math.floor((-n / z - 3.0) / 4.0)
    stop = math.floor((n / z - 1.0) / 4.0)
    for k in range(start, stop + 1):
        second += (normal_distribution.cdf((4 * k + 3) * z / sqrt_n) -
                   normal_distribution.cdf((4 * k + 1) * z / sqrt_n))
    return float(1.0 - first + second), z


def test_13_cumulative_sums(bits: np.ndarray) -> TestResult:
    name = "Cumulative Sums (Forward/Reverse)"
    n = int(bits.size)
    if n < 100:
        return not_applicable(13, name, "At least 100 bits are required.")
    p_forward, z_forward = _cusum_p_value(bits)
    p_reverse, z_reverse = _cusum_p_value(bits[::-1])
    return result_from_p_values(13, name,
                                {"forward": p_forward, "reverse": p_reverse},
                                f"z_forward={z_forward}; z_reverse={z_reverse}")


def _random_walk_and_cycles(bits: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    walk = np.concatenate((np.array([0], dtype=np.int64),
                           np.cumsum(bits.astype(np.int64) * 2 - 1)))
    if walk[-1] != 0:
        walk = np.concatenate((walk, np.array([0], dtype=np.int64)))
    zero_indices = np.flatnonzero(walk == 0)
    return walk, zero_indices, int(zero_indices.size - 1)


def test_14_random_excursions(bits: np.ndarray) -> TestResult:
    name = "Random Excursions"
    n = int(bits.size)
    walk, zeros, cycles_number = _random_walk_and_cycles(bits)
    constraint = max(0.005 * math.sqrt(n), 500.0)
    if cycles_number < constraint:
        return not_applicable(14, name,
                              f"Only {cycles_number} cycles; at least {constraint:.1f} are required.")
    states = (-4, -3, -2, -1, 1, 2, 3, 4)
    probabilities = {
        1: np.array([0.5, 0.25, 0.125, 0.0625, 0.03125, 0.03125]),
        2: np.array([0.75, 0.0625, 0.046875, 0.03515625, 0.0263671875, 0.0791015625]),
        3: np.array([0.8333333333, 0.02777777778, 0.02314814815,
                     0.01929012346, 0.01607510288, 0.0803755143]),
        4: np.array([0.875, 0.015625, 0.013671875, 0.01196289063,
                     0.01046752930, 0.0732727051]),
    }
    visit_bins = {state: np.zeros(6, dtype=np.int64) for state in states}
    for cycle_index in range(cycles_number):
        cycle = walk[zeros[cycle_index] + 1:zeros[cycle_index + 1] + 1]
        for state in states:
            visits = int(np.count_nonzero(cycle == state))
            visit_bins[state][min(visits, 5)] += 1
    p_values: dict[str, float] = {}
    for state in states:
        expected = cycles_number * probabilities[abs(state)]
        chi_square = float(np.sum((visit_bins[state] - expected) ** 2 / expected))
        p_values[f"state_{state:+d}"] = float(special.gammaincc(2.5, chi_square / 2.0))
    failed = sum(p < ALPHA for p in p_values.values())
    return result_from_p_values(14, name, p_values,
                                f"cycles={cycles_number}; states=8; subtests_failed={failed}")


def test_15_random_excursions_variant(bits: np.ndarray) -> TestResult:
    name = "Random Excursions Variant"
    n = int(bits.size)
    walk, _, cycles_number = _random_walk_and_cycles(bits)
    constraint = max(0.005 * math.sqrt(n), 500.0)
    if cycles_number < constraint:
        return not_applicable(15, name,
                              f"Only {cycles_number} cycles; at least {constraint:.1f} are required.")
    # Remove the synthetic start/end zeros; nonzero state visit counts are unchanged.
    p_values: dict[str, float] = {}
    for state in tuple(range(-9, 0)) + tuple(range(1, 10)):
        visits = int(np.count_nonzero(walk == state))
        denominator = math.sqrt(2.0 * cycles_number * (4.0 * abs(state) - 2.0))
        p_values[f"state_{state:+d}"] = float(
            special.erfc(abs(visits - cycles_number) / denominator)
        )
    failed = sum(p < ALPHA for p in p_values.values())
    return result_from_p_values(15, name, p_values,
                                f"cycles={cycles_number}; states=18; subtests_failed={failed}")


TESTS: tuple[Callable[[np.ndarray], TestResult], ...] = (
    test_01_frequency,
    test_02_block_frequency,
    test_03_runs,
    test_04_longest_run,
    test_05_binary_matrix_rank,
    test_06_dft,
    test_07_non_overlapping_template,
    test_08_overlapping_template,
    test_09_maurer_universal,
    test_10_linear_complexity,
    test_11_serial,
    test_12_approximate_entropy,
    test_13_cumulative_sums,
    test_14_random_excursions,
    test_15_random_excursions_variant,
)


def run_all_tests(bits: np.ndarray) -> list[TestResult]:
    results: list[TestResult] = []
    for index, test_function in enumerate(TESTS, start=1):
        label = test_function.__name__.replace("test_", "", 1)
        print(f"  [{index:02d}/15] {label} ... ", end="", flush=True)
        started = time.perf_counter()
        try:
            result = test_function(bits)
        except Exception as exc:  # Keep the remaining tests running and report the exact failure.
            result = TestResult(index, label, "ERROR", {},
                                f"{type(exc).__name__}: {exc}")
        result.elapsed_seconds = time.perf_counter() - started
        print(f"{result.status} ({result.elapsed_seconds:.2f} s)")
        results.append(result)
    return results


def _format_p_value(value: float | None) -> str:
    return "" if value is None else f"{value:.12g}"


def write_outputs(
    output_folder: Path,
    all_results: list[tuple[str, int, int, list[TestResult]]],
) -> None:
    output_folder.mkdir(parents=True, exist_ok=True)
    summary_path = output_folder / "nist_summary.csv"
    detailed_path = output_folder / "nist_detailed_results.csv"

    with summary_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "input_file", "total_bits", "test_number", "test_name", "status",
            "summary_p_value", "p_value_count", "alpha", "elapsed_seconds", "details",
        ])
        for filename, total_bits, _, results in all_results:
            for result in results:
                writer.writerow([
                    filename, total_bits, result.number, result.name, result.status,
                    _format_p_value(result.summary_p_value), len(result.p_values), ALPHA,
                    f"{result.elapsed_seconds:.6f}", result.details,
                ])

    with detailed_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "input_file", "test_number", "test_name", "subtest", "p_value", "status", "alpha",
        ])
        for filename, _, _, results in all_results:
            for result in results:
                if result.p_values:
                    for subtest, p_value in result.p_values.items():
                        writer.writerow([
                            filename, result.number, result.name, subtest,
                            _format_p_value(p_value), "PASS" if p_value >= ALPHA else "FAIL", ALPHA,
                        ])
                else:
                    writer.writerow([
                        filename, result.number, result.name, "", "", result.status, ALPHA,
                    ])

    for filename, total_bits, ones, results in all_results:
        report_name = f"{Path(filename).stem}_nist_report.txt"
        report_path = output_folder / report_name
        zeros = total_bits - ones
        lines = [
            "NIST SP 800-22 Rev. 1a - 15 Test Categories",
            "=" * 68,
            f"Input file : {filename}",
            f"Total bits : {total_bits}",
            f"Zeros      : {zeros}",
            f"Ones       : {ones}",
            f"P(1)       : {ones / total_bits:.8%}",
            f"Alpha      : {ALPHA}",
            "",
            "PASS/FAIL follows the P-value(s) shown in the report table.",
            "For test 7, the table uses template B=000000001 while the detailed CSV",
            "retains all 148 official templates. Inspect the detailed CSV for subtests.",
            "NOT_APPLICABLE means that the sequence did not meet a test requirement.",
            "",
        ]
        for result in results:
            summary_p = _format_p_value(result.summary_p_value) or "N/A"
            lines.extend([
                f"{result.number:02d}. {result.name}",
                f"    Status       : {result.status}",
                f"    Summary P-value: {summary_p}",
                f"    P-values     : {len(result.p_values)}",
                f"    Time         : {result.elapsed_seconds:.3f} s",
                f"    Details      : {result.details}",
            ])
            if 0 < len(result.p_values) <= 18:
                for subtest, p_value in result.p_values.items():
                    sub_status = "PASS" if p_value >= ALPHA else "FAIL"
                    lines.append(f"      {subtest}: {p_value:.12g} [{sub_status}]")
            lines.append("")
        report_path.write_text("\n".join(lines), encoding="utf-8")

    print("\nResults saved to:")
    print(f"  {summary_path}")
    print(f"  {detailed_path}")
    for filename, _, _, _ in all_results:
        print(f"  {output_folder / (Path(filename).stem + '_nist_report.txt')}")


def main() -> None:
    script_folder = Path(__file__).resolve().parent
    output_folder = script_folder / OUTPUT_FOLDER_NAME
    print("NIST SP 800-22 Rev. 1a - all 15 test categories")
    print(f"Significance level alpha = {ALPHA}\n")

    missing = [name for name in INPUT_FILENAMES if not (script_folder / name).is_file()]
    if missing:
        print("ERROR: The following input file(s) were not found beside this Python file:")
        for name in missing:
            print(f"  - {name}")
        print("\nPut this .py file and both bitstream files in the same folder, then Run again.")
        input("\nPress Enter to close...")
        return

    all_results: list[tuple[str, int, int, list[TestResult]]] = []
    for filename in INPUT_FILENAMES:
        input_path = script_folder / filename
        print("\n" + "=" * 72)
        print(f"Reading {filename} as text (one 0/1 per line)...")
        try:
            bits = read_text_bitstream(input_path)
        except Exception as exc:
            print(f"ERROR: {exc}")
            input("\nPress Enter to close...")
            return
        available_bits = int(bits.size)
        if available_bits < TEST_BIT_COUNT:
            print(f"ERROR: {filename} contains only {available_bits:,} bits, but "
                  f"this comparison requires {TEST_BIT_COUNT:,} bits.")
            input("\nPress Enter to close...")
            return
        bits = bits[:TEST_BIT_COUNT]
        total_bits = int(bits.size)
        ones = int(bits.sum())
        print(f"Available bits: {available_bits:,}; testing the first {total_bits:,} bits only.")
        print(f"Test sequence: zeros={total_bits - ones:,}, "
              f"ones={ones:,}, P(1)={ones / total_bits:.6%}")
        results = run_all_tests(bits)
        all_results.append((filename, total_bits, ones, results))

    write_outputs(output_folder, all_results)
    print("\nFinished. Open nist_summary.csv first.")


if __name__ == "__main__":
    main()

