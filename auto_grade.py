"""Conservative selection of shot-locked SDR looks from source measurements."""


def compressed_luma(low, mean, high, peak):
    """Use full-range 8-bit Y' statistics from the downsampled active picture.

    A low average is also common in an intentional night scene. Look for a
    lifted shadow floor *and* usable texture confined to the bottom quarter
    of the range, with no bright practicals anywhere in the sampled frame.
    """
    return (low >= 5 and 12 <= mean <= 40 and 24 <= high <= 64
            and high - low >= 12 and peak <= 112)


def use_cinema(flags, means, elapsed_us, headroom):
    """Select one transform for the whole shot, including its first/last frames."""
    if headroom or len(flags) < 5 or elapsed_us < 300_000:
        return False
    # Reject flashes, fades, or uncertainty at a cut. Outliers are intentional
    # vetoes: a lamp or a visible fade deserves the original lighting treatment.
    return all(flags) and min(means) >= .72 * max(means)
