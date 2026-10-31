# ISBN data validation

Diagnosis of the `isbn` (ISBN-10) and `isbn13` columns in `books.csv` (10,000 rows) and `books-batch2.csv` (31 rows). Every row was checked; nothing in either file has been changed.

## How the values were checked

Each value was read as raw text, exactly as stored in the CSV, and tested against the ISBN rules:

- **ISBN-10:** exactly 10 characters: 9 digits plus a check character that is a digit or an uppercase `X` (meaning 10). The check passes when `10·d1 + 9·d2 + … + 1·d10` is divisible by 11.
- **ISBN-13:** exactly 13 digits starting with `978` or `979`. The check passes when the digits, weighted alternately 1 and 3, add up to a multiple of 10.
- **Agreement:** when a row has both, the ISBN-13 should be `978` + the first 9 digits of the ISBN-10 + a newly calculated check digit.

A value that passes these rules has a valid format and checksum. That does not prove a real book has that ISBN. That would need a lookup in an ISBN registry, which I didn't do.

## Summary

| | `books.csv` | `books-batch2.csv` |
|---|---:|---:|
| **ISBN-10 valid as stored** | 2,690 | 12 |
| ISBN-10 valid once leading zeros are restored | 6,587 | 7 |
| ISBN-10 invalid as stored | 23 | 7 (3 are formatting only) |
| ISBN-10 empty | 700 | 5 |
| **ISBN-13 valid as stored** | **0** | 9 |
| ISBN-13 stored in scientific notation (last digit lost) | 9,382 | 14 |
| ISBN-13 not an ISBN, or the wrong length | 33 | 0 |
| ISBN-13 empty | 585 | 8 |
| Both columns empty | 565 | 3 |

The two biggest problems affect almost every row in `books.csv`. Its ISBN-10s lost their leading zeros, and **no ISBN-13 in `books.csv` is usable as stored**, because each one was converted to a decimal number and rounded.

## ISBN-10 problems (`isbn` column)

### 1. Leading zeros dropped

- **Rows:** `books.csv` 6,587 (5,563 missing one zero, 913 missing two, 111 missing three). `books-batch2.csv` 7.
- **Example:** book 1, *The Hunger Games*, stores `439023483`; the real ISBN is `0439023483`. Book 4, *To Kill a Mockingbird*, stores `61120081` instead of `0061120081`.
- **What's wrong:** The value is 7–9 digits long instead of 10. Putting the zeros back makes every one of these pass the checksum, so no other information was lost.
- **Probable cause:** The column was treated as a number instead of text, so leading zeros disappeared. The 814 ISBN-10s ending in `X`, such as book 18's `043965548X`, kept their zeros. A value with a letter in it can't be read as a number, so it stayed text. That's what points to numeric conversion as the cause.

### 2. Invalid ISBN-10s (wrong check digit)

- **Rows:** `books.csv` 23: 14 short values that still fail after the zeros are restored, plus 9 full-length values that fail. `books-batch2.csv` 1 (book 10007).
- **Example:** book 1543, *The Racketeer*, stores `385535144`. Even with the leading zero restored (`0385535144`), it fails the checksum; the correct ISBN-10 is `0385535147`. Book 2778, *Two-Way Street*, stores `1416913184`, which also fails.
- **What's wrong:** The last character isn't the correct check digit.
- **Probable cause:** For 17 of the 23 in `books.csv`, the value is the ISBN-13 with `978` removed and the ISBN-13's check digit left in place. For *The Racketeer*, `978-0-385-53514-4` became `038553514`**`4`** instead of `038553514`**`7`**. ISBN-10 and ISBN-13 use different check-digit formulas, so the ISBN-10 check digit needed to be recalculated. Book 10007's `1854433017` in batch 2 has no matching ISBN-13 to compare against, so it's probably a typo.

### 3. Formatting problems (batch 2 only)

Each of these happens once, in `books-batch2.csv`:

| Problem | Book | Stored value | What's wrong | Probable cause |
|---|---|---|---|---|
| Hyphens | 10002 *Salt and Ember* | `0-450-19827-8` | Hyphens aren't part of the value. Without them it's valid: `0450198278`. | Copied in the printed style, like on a book's copyright page |
| Spaces | 10003 *A Field Guide to Quiet Places* | `␣162 001 4750␣` | Spaces at the start, end and inside. Without them it's `1620014750`, which is valid. | Hand-typed or pasted |
| Lowercase `x` | 10004 *Winter Orchard* | `031200009x` | The check character must be an uppercase `X`. With `X` it's valid. | Hand-typed |
| Letter in the middle | 10008 *The Glass Aviary* | `04390A3483` | Only the last character may be a letter. This is *The Hunger Games*' ISBN `0439023483` with the `2` replaced by `A`. | Typo, or a value made up for this test data |
| Too long | 10009 *Borrowed Light* | `15287463097` | 11 digits. It's book 10001's ISBN `1528746309` with an extra `7`. | Typo, or a value made up for this test data |
| ISBN-13 in the ISBN-10 column | 10014 *An Atlas of Small Kindnesses* | `9781982147600` | A valid ISBN-13 in the wrong column, while `isbn13` is empty. | Values put in the wrong columns |

### 4. Empty

- **Rows:** `books.csv` 700 (565 also have no ISBN-13). `books-batch2.csv` 5.
- **Example:** book 106, *Bossypants*, and book 121, *Lolita*, have neither ISBN.
- **Probable cause:** The source data had no ISBN for that edition. Ebook-only and some foreign editions often don't have one.

## ISBN-13 problems (`isbn13` column)

### 5. Stored in scientific notation, last digit lost

- **Rows:** `books.csv` 9,382, which is every `978`/`979` value in the file. The non-ISBN codes in problems 7 and 8 were converted the same way. `books-batch2.csv` 14 (all rows copied from `books.csv`, plus book 10012).
- **Example:** book 1, *The Hunger Games*, stores `9.78043902348e+12`. The real ISBN-13 is `9780439023481`, but this value equals `9780439023480`. The final `1` was rounded off. Book 11, *The Kite Runner*, stores `9.78159448e+12`; this is also rounded to 12 digits, and the trailing zeros just aren't shown.
- **What's wrong:** The value is a decimal number rounded to 12 significant digits, so the 13th digit, the check digit, is gone. Rounding can also change the 12th digit. A number exactly halfway goes to the even ten: `…25` became `…20` and `…35` became `…40`. That's true for all 949 such rows where the ISBN-10 shows the real number.
- **Probable cause:** Empty cells in the column made it be read as decimal numbers instead of text or integers. The file was then saved with 12-digit precision. Python 2's float-to-text conversion did exactly this, and so does Excel with large numbers.
- **What can be recovered**, in `books.csv`:

  | Situation | Rows |
  |---|---:|
  | A usable ISBN-10 in the same row produces exactly this rounded value | 9,238 |
  | No usable ISBN-10, but only one checksum-valid ISBN-13 rounds to this value | 45 |
  | No usable ISBN-10, and two checksum-valid ISBN-13s fit, so it can't be resolved | 84 |
  | No usable ISBN-10, and no checksum-valid ISBN-13 rounds to this value | 2 |
  | A usable ISBN-10 exists but points to a different ISBN-13 (see problem 6) | 13 |
  | **Total** | **9,382** |

### 6. ISBN-13 and ISBN-10 point to different numbers

- **Rows:** `books.csv` 13. `books-batch2.csv` 1 (book 10011).
- **Example:** book 622, *Nights in Rodanthe*, has ISBN-10 `0446612707`, which converts to `9780446612708`. Its `isbn13` is `9.78055381395e+12` (`978-0-553-81395-…`), a completely different number. In batch 2, book 10011 *Tides of Marrow Bay* has `1555550126` (→ `9781555550127`) and `9781555550134`. Both pass their checksums but don't match each other.
- **What's wrong:** Each value may be valid on its own, but they can't both be this row's ISBN.
- **Probable cause:** The two columns were filled from different editions of the same book, such as a US paperback and a UK edition. Book 1106, *The Great Divorce*, has a `979-0…` value; that prefix is reserved for printed music (ISMN), not books. The data can't tell us which column is right.

### 7. 13-digit codes that aren't ISBNs

- **Rows:** `books.csv` 25. `books-batch2.csv` 0.
- **Examples:**
  - 22 rows start with `2940`, such as book 1165, *Wait for You*: `2.94001623192e+12`. These look like Barnes & Noble ebook IDs, which use the same 13-digit layout but aren't ISBNs.
  - 2 rows start with `622`, such as book 5647, an Arabic title: `6.22110201718e+12`. `622` is the barcode prefix for Egypt, so these are retail barcodes.
  - 1 row is book 8941, *Cross My Heart*: `1.2300000055e+12`, which looks like a placeholder.
- **Probable cause:** The source stored "any 13-digit product code" in the ISBN-13 field when an edition had no ISBN.

### 8. Wrong length

All of these are in `books.csv`:

| Problem | Rows | Example | Probable cause |
|---|---:|---|---|
| UPC barcode (12 digits) instead of an ISBN | 6 | book 958, *Anne of Green Gables* box set: `76783609419.0` = UPC `076783609419`. Book 2894, *Sailor Moon*: `6.45573003012e+11` | Product barcodes for box sets and imported items. All 6 pass the UPC checksum. Some also lost a leading zero. |
| ISBN-13 cut short | 1 | book 1443, *The Secret of the Nagas*: `97893806587.0`, the first 11 digits of `9789380658797` | Truncated in the source data |
| ISBN-10 in the ISBN-13 column | 1 | book 8174, *Washington's Crossing*: `195170342.0` is the same as its ISBN-10 `0195170342` | Value copied into the wrong column |

The `.0` at the end of these values confirms that the column was converted to decimal numbers (see problem 5).

### 9. Empty

- **Rows:** `books.csv` 585 (565 also have no ISBN-10). `books-batch2.csv` 8.
- **Probable cause:** Same as problem 4. Book 25 in batch 2 is a special case; see the next section.

## Row-level problems in `books-batch2.csv`

These go beyond single values:

- **Duplicate row:** book 10001, *The Lantern Keeper's Daughter*, appears twice with exactly the same values. This is probably a copy-paste or merge mistake.
- **ISBN belongs to another book:** book 10013, *Paper Moons*, has ISBN-10 `1416524797`, which is the ISBN of book 9, *Angels & Demons*, in both files. Its checksum is valid, which is why a checksum test alone wouldn't catch it.
- **ISBNs removed:** book 25, *Harry Potter and the Deathly Hallows*, has both columns empty in batch 2. `books.csv` has `545010225` and `9.78054501022e+12` for it.
- **Formatting differs from `books.csv`:** book 24, *Harry Potter and the Goblet of Fire*, is stored correctly in batch 2 (`0439139600`, `9780439139601`) but damaged in `books.csv` (`439139600`, `9.7804391396e+12`). Joining the files on these columns as raw text will treat them as different values.
- **Rows that look synthetic:** the 15 rows for books 10001–10014 all have identical rating statistics (4.01, 1,200 ratings), `goodreads_book_id`s in the 90010001–90010014 range and no images. They look like test rows made up for this assignment. Their ISBNs that pass checksums may not belong to real books.

## Row-by-row: `books-batch2.csv`

✅ valid · 🔧 valid once the formatting is fixed · ⚠️ value lost or doesn't match · ❌ invalid · — empty

| Book | Title | `isbn` | ISBN-10 result | `isbn13` | ISBN-13 result |
|---|---|---|---|---|---|
| 106 | Bossypants | — | — empty | — | — empty |
| 10008 | The Glass Aviary | `04390A3483` | ❌ letter in the middle | — | — empty |
| 24 | Harry Potter and the Goblet of Fire | `0439139600` | ✅ | `9780439139601` | ✅ matches its ISBN-10 |
| 132 | The Five People You Meet in Heaven | `1401308589` | ✅ | `9.78140130858e+12` | ⚠️ scientific notation; recoverable from its ISBN-10 |
| 10012 | The Long Walk Home | `441773028` | 🔧 missing 1 leading zero | `9.78044177302e+12` | ⚠️ scientific notation; recoverable |
| 11 | The Kite Runner | `1594480001` | ✅ | `9.78159448e+12` | ⚠️ scientific notation; recoverable |
| 2 | Harry Potter and the Sorcerer's Stone | `439554934` | 🔧 missing 1 leading zero | `9.78043955493e+12` | ⚠️ scientific notation; recoverable |
| 46 | Water for Elephants | `1565125606` | ✅ | `9.7815651256e+12` | ⚠️ scientific notation; recoverable |
| 10006 | Notes from the Night Ferry | — | — empty | `9798351220413` | ✅ (`979` prefix) |
| 10007 | Small Hours | `1854433017` | ❌ wrong check digit | — | — empty |
| 14 | Animal Farm | `452284244` | 🔧 missing 1 leading zero | `9.78045228424e+12` | ⚠️ scientific notation; recoverable |
| 13 | 1984 | `451524934` | 🔧 missing 1 leading zero | `9.78045152494e+12` | ⚠️ scientific notation; recoverable |
| 10001 | The Lantern Keeper's Daughter | `1528746309` | ✅ | `9781528746304` | ✅ matches (**duplicate row**) |
| 10001 | The Lantern Keeper's Daughter | `1528746309` | ✅ | `9781528746304` | ✅ matches (**duplicate row**) |
| 10011 | Tides of Marrow Bay | `1555550126` | ✅ | `9781555550134` | ⚠️ valid, but doesn't match its ISBN-10 |
| 3 | Twilight | `316015849` | 🔧 missing 1 leading zero | `9.78031601584e+12` | ⚠️ scientific notation; recoverable |
| 10014 | An Atlas of Small Kindnesses | `9781982147600` | ❌ an ISBN-13 in the ISBN-10 column | — | — empty |
| 18 | Harry Potter and the Prisoner of Azkaban | `043965548X` | ✅ | `9.78043965548e+12` | ⚠️ scientific notation; recoverable |
| 10009 | Borrowed Light | `15287463097` | ❌ 11 digits | — | — empty |
| 9 | Angels & Demons | `1416524797` | ✅ | `9.78141652479e+12` | ⚠️ scientific notation; recoverable |
| 10010 | Untitled Manuscript | — | — empty | — | — empty |
| 34 | Fifty Shades of Grey | `1612130291` | ✅ | `9.78161213029e+12` | ⚠️ scientific notation; recoverable |
| 10013 | Paper Moons | `1416524797` | ⚠️ valid, but it's *Angels & Demons*' ISBN | — | — empty |
| 10005 | The Cartographer's Error | — | — empty | `9780062398123` | ✅ |
| 25 | Harry Potter and the Deathly Hallows | — | ⚠️ empty here, but present in `books.csv` | — | ⚠️ empty here, but present in `books.csv` |
| 10004 | Winter Orchard | `031200009x` | 🔧 lowercase `x` | `9780312000097` | ✅ matches |
| 134 | City of Glass | `1416914307` | ✅ | `9.7814169143e+12` | ⚠️ scientific notation; recoverable |
| 1 | The Hunger Games | `439023483` | 🔧 missing 1 leading zero | `9.78043902348e+12` | ⚠️ scientific notation; recoverable |
| 10002 | Salt and Ember | `0-450-19827-8` | 🔧 hyphens | `9780450198274` | ✅ matches |
| 10003 | A Field Guide to Quiet Places | `␣162 001 4750␣` | 🔧 spaces | `9781620014752` | ✅ matches |
| 4 | To Kill a Mockingbird | `61120081` | 🔧 missing 2 leading zeros | `9.78006112008e+12` | ⚠️ scientific notation; recoverable |

## What this means for fixing (later)

- **Read both columns as text.** If they're loaded as numbers, the leading zeros and the rounded ISBN-13s will be damaged again.
- **Rebuild ISBN-13s from the ISBN-10s.** The stored ISBN-13s can't be trusted, but 9,238 rows can be rebuilt from their ISBN-10. Another 45 can be recovered by testing which checksum-valid ISBN-13 rounds to the stored value. The other 86 rounded values can't be resolved from this data alone.
- **Decide what to do with conflicts and non-ISBN codes.** The 14 rows where the two columns disagree, and the 31 non-ISBN codes (Barnes & Noble IDs, store barcodes, UPCs), need a decision: leave them empty, keep them in a separate "other ID" column, or look them up.
- **Don't trust checksums alone.** A checksum doesn't catch an ISBN that belongs to another book (*Paper Moons*) or a duplicate row.
