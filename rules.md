# ISBN rules (draft)

These are the rules I'm using to decide whether an ISBN in `books.csv` and `books-batch2.csv` is valid, and what I plan to do when it isn't. I haven't changed the data yet. This is just the plan.

## Rule 1: Treat ISBNs as text, not numbers

An ISBN is an identifier, not an amount, so I always read the `isbn` and `isbn13` columns as text. When they get read as numbers, two things break:

- Leading zeros disappear (`0439023483` turns into `439023483`).
- Long ISBN-13s get rounded and stored like `9.78043902348e+12`, which loses the last digit.

Most of the bad values in `books.csv` came from this.

## Rule 2: What a valid ISBN-10 looks like

- It's exactly 10 characters long.
- The first 9 are digits.
- The last one is a digit or a capital `X` (`X` means 10).
- The checksum works: multiply the digits by 10, 9, 8 … down to 1, add them up, and the total has to divide evenly by 11.

Example: `0439023483` gives a total of 176, and 176 = 11 × 16, so it's valid.

## Rule 3: What a valid ISBN-13 looks like

- It's exactly 13 digits, with no letters.
- It starts with `978` or `979`. Other 13-digit numbers are barcodes or store IDs, not ISBNs.
- It doesn't start with `9790`. Those numbers are ISMNs, which identify printed music. The ISBN Users' Manual says printed music gets an ISMN, not an ISBN (sections 4 and 16.6).
- The checksum works: multiply the digits by 1, 3, 1, 3 … in turn, add them up, and the total has to be a multiple of 10.

Example: `9780439023481` gives a total of 100, so it's valid.

## Rule 4: The two columns have to agree

If a row has both an ISBN-10 and an ISBN-13, they should be the same book. To check, I take the first 9 digits of the ISBN-10, put `978` in front, and work out a new check digit. That should match the ISBN-13 exactly. If it doesn't, at least one of them is wrong, and I can't tell which from the data alone.

## Rule 5: Formatting problems I'll fix

These problems don't change the ISBN itself, so I'll clean them up:

- Add back missing leading zeros, but only if the result passes the checksum.
- Remove hyphens and spaces.
- Change a lowercase `x` to `X`.
- Rebuild a rounded ISBN-13 from the ISBN-10 in the same row, when that ISBN-10 is valid.

## Rule 6: Values I won't try to guess

I'll mark these as invalid instead of making something up:

- A wrong check digit, a letter in the middle, or the wrong number of digits.
- A 13-digit code that doesn't start with `978` or `979`, or a 12-digit UPC barcode.
- A rounded ISBN-13 that could be more than one valid number.
- Rows where the ISBN-10 and ISBN-13 don't match.

An empty cell stays empty, unless the other column lets me calculate it exactly (see Rule 8). A missing ISBN isn't the same as a wrong one.

## Rule 7: A valid checksum isn't proof

The checksum only shows that the number is built correctly. It can't tell me if the ISBN belongs to the right book, or if the book is real. So I'll also check:

- The same ISBN showing up on two different books (like *Paper Moons* using *Angels & Demons*' ISBN).
- Rows that are exact duplicates.
- Books whose ISBNs are in one file but missing from the other.

## Rule 8: Repair, reject, or warn

I almost never throw out a whole row. The ISBN is only one column, and the title, authors and ratings are still useful. When an ISBN is bad, I set the cleaned value to empty, keep the original value in its own column, and add a warning. The only rows I drop are exact duplicates.

Every row gets a status, so anyone using the data can decide what to trust:

- `ok`: valid as it was.
- `repaired`: only one fix was possible, and something independent confirmed it.
- `inferred`: I fixed it based on an assumption, so it might be wrong.
- `rejected`: I couldn't fix it, so the cleaned value is empty.

| Problem | What I do | Why |
|---|---|---|
| ISBN-10 lost its leading zeros | Repair | Only one fix is possible, and the checksum really tests it because I didn't choose the check digit. |
| Hyphens, spaces, lowercase `x` | Repair | The characters stay the same; only the formatting changes. |
| ISBN-13 rounded, valid ISBN-10 in the row | Repair | Calculating it from the ISBN-10 is exact, and the result has to round to the stored value. |
| ISBN-13 empty or not an ISBN, valid ISBN-10 in the row | Repair | An ISBN-10 and its ISBN-13 are the same identifier, so this is a calculation. |
| ISBN-13 in the ISBN-10 column | Repair, with a warning | Move it to `isbn13` and calculate the ISBN-10 from it. Warn because the value was in the wrong place. |
| ISBN-13 rounded, only one valid number fits | Inferred, with a warning | Passing the checksum proves nothing here, because one of the 10 possible last digits always passes. It only works if the original value was a real ISBN. |
| ISBN-10 and ISBN-13 don't match | Keep the ISBN-10, calculate the ISBN-13 from it, keep the old ISBN-13 as the original value, warn | I can't tell which one is right. The ISBN-10 is at least stored exactly, and this keeps the two columns consistent. The other number might be a different edition, so I don't throw it away. |
| Wrong check digit | Reject | The mistake could be in any digit. A new check digit would always pass, but might give me a different book's ISBN. |
| Letter in the middle, too many digits, ISBN-13 cut short | Reject | I'd have to choose what the missing or wrong digits were. |
| ISBN-13 rounded, two valid numbers fit | Reject | Picking one would be a coin flip. |
| Store IDs and UPC barcodes | Move to an `other_id` column | They're real values, just not ISBNs. |
| Same ISBN on two different books | Warn on both rows | Which book really owns it is a question about the real world, not the data. |
| Exact duplicate row | Drop the extra copy | Nothing is lost. |

**When is a repair really a guess?** When I have to choose between possible answers, or when it only works if my theory about what went wrong is correct. Some fixes look safe and aren't. For example, I could recalculate a wrong ISBN-10 check digit, and it would always pass, but that doesn't make it right.

## Rule 9: What makes two rows the same book

An ISBN identifies one product, not the book: one edition, in one format, in one language, from one publisher (ISBN Users' Manual, sections 1 and 6). The hardcover, paperback, EPUB, audiobook and UK printing of the same book each have their own ISBN. This dataset is about books (all editions together), so the ISBN can't decide whether two rows are the same book on its own:

- Different ISBNs don't mean different books. They could be different editions.
- The same ISBN doesn't mean the same book. *Paper Moons* has *Angels & Demons*' ISBN.

I match on the strongest ID I have, in this order:

1. `work_id`: this is Goodreads' ID for the book across all its editions. Each row in `books.csv` has a different one.
2. `goodreads_book_id`: the ID of one edition.
3. `book_id`: only when both files come from the same source. This works for batch 2. Its 16 rows that share a `book_id` with `books.csv` also have the same title, authors and `work_id`.
4. The cleaned ISBN: this means the same edition, which is good evidence but not proof.

If the stronger ID disagrees with the weaker one, the stronger one wins and the row gets a warning.

## Rule 10: When a book has no usable ISBN

In this data, that's fine. All 565 books in `books.csv` with no ISBN still have a `work_id`.

If a row has no ID at all, the only thing left is the title and author, and that isn't safe:

- 154 titles are used by more than one author. There are three different *Twilight*s, by Stephenie Meyer, Meg Cabot and Erin Hunter.
- Some rows have the same title and author but are different books, like the separate volumes of *Avatar: The Last Airbender*.
- Titles change between editions (*Sorcerer's Stone* vs *Philosopher's Stone*), and they have series tags like "(Harry Potter, #1)".

So without an ID, I clean up the title (lowercase it, remove the series tag and extra spaces), use only the first author, and match on title + author + original publication year. A match like that is only a possible match. I flag it for someone to check and never merge it automatically. A title alone is never enough.

## Rule 11: When the same book has different values

Even when two rows are definitely the same book, their values can disagree. In 4 of the 16 shared rows, batch 2 has a higher `ratings_count` than `books.csv`, so batch 2 is probably a newer copy of the data. I need a rule for which value wins, but "the newer file always wins" can't apply to every column: batch 2 lost the ISBNs for *Deathly Hallows*. So the rule depends on the kind of column. Rule 12 has the details.

## Rule 12: When a book that's already loaded arrives again

Just inserting every row doesn't work. Depending on how the table is set up, I'd get two copies of the same book, the load would stop with an error, or the new row would replace the old one completely and wipe out values like *Deathly Hallows*' ISBNs.

Here's what batch 2 actually sends for the 16 books that are already in `books.csv`:

- 10 are exactly the same.
- 1 (*Goblet of Fire*) only looks different. Its ISBNs are formatted correctly in batch 2, so after cleaning they're the same values.
- 1 (*Deathly Hallows*) lost its ISBNs. That's missing data, not a change.
- 4 have more ratings. `ratings_count`, `work_ratings_count` and `ratings_5` all went up by the same amount.

So my steps are:

1. **Clean first, then match.** I clean the incoming row the same way, then look it up by `work_id` (Rule 9). If I compared before cleaning, *Goblet of Fire* would look like it changed.
2. **Sort each row.** A new book gets added. An identical book doesn't change anything, but I note which file it came from. A changed book is handled column by column:

   | Kind of column | What I do | Why |
   |---|---|---|
   | IDs (`work_id`, `goodreads_book_id`) | Never change them, and flag it if they differ | If an ID changes, it's not the same book or the match was wrong |
   | Descriptive (title, authors, year, ISBNs) | Never replace a value with an empty one. If a non-empty value is different, apply it and flag it | This stops the *Deathly Hallows* problem, and these changes should be checked |
   | Counts (ratings) | Take the newer value, and flag it if a count goes down | Counts normally only go up |
   | Calculated (`average_rating`, `work_ratings_count`) | Recalculate them from the star counts instead of copying them | The source doesn't always update them |

3. **Keep the history.** I don't overwrite a value without saving the old one, the new one, which file it came from, and when I loaded it. Then I can undo a bad load and see what a book looked like on any date.
4. **Know which file is newer.** Neither file says when its data was collected, so I record a date for each file when I load it. If I loaded an older file after a newer one, "newer wins" would put back the old, lower counts. Flagging counts that go down catches most of those mistakes.
5. **Loading the same file twice should change nothing.** I remove duplicates inside a file first (book 10001 appears twice). After that, a second load of the same file finds every row identical.

Things I noticed while checking:

- The calculated values really can go stale. In `books.csv`, `average_rating` always matches the star counts. But for *The Five People You Meet in Heaven* (book 132), batch 2's new star counts give an average of 3.9051, which should round to 3.91, while the file still says 3.9.
- All the new ratings in batch 2 are 5 stars, and `ratings_1` to `ratings_4` didn't change at all. Real updates usually spread across all five, so this might be edited test data.

## Rule 13: The load report

Every load ends with a report written for someone reading it the next morning. They need three answers, in this order: did it finish, does every row add up, and what do they have to do today. Everything else goes in a linked file.

The numbers below are what I expect from loading batch 2 after `books.csv`, based on my checks. I haven't built the loader yet, so they aren't from a real run.

### 1. The result, in one line

The first line says one of three things:

- **Loaded:** every row is accounted for and nothing needs attention.
- **Loaded with warnings:** every row is accounted for, but some values were rejected or flagged.
- **Failed:** the load stopped and nothing changed, because it's all-or-nothing. It says why, and that it's safe to run again.

For batch 2: *Loaded with warnings: 31 rows. 14 new books, 4 updated, 2 need a decision, 3 ISBNs need looking up.*

I never just say "success" when values were rejected.

### 2. A count that adds up

| Rows in the file | 31 |
|---|---:|
| New books added | 14 |
| Books updated | 4 |
| Already loaded, nothing changed | 12 |
| Duplicate rows skipped | 1 |
| **Total** | **31** |

The total has to equal the number of rows in the file. If it doesn't, rows went missing without an error, and that's the most important thing the report can show. I also show the number of books before and after (10,000 → 10,014).

There's no "rejected rows" line, because I don't reject rows (Rule 8). Instead, I count each ISBN column by status (`ok`, `repaired`, `inferred`, `rejected`, empty) next to the same counts from the last load. A big jump, like 0 rejected to 400, usually means the source changed its format.

### 3. What to do

This is the main part. It's grouped by what the reader has to do, with the most urgent first, and each group has a count, an example and a link to the full list.

- **Needs a decision:** *Paper Moons* has *Angels & Demons*' ISBN. *Tides of Marrow Bay*'s two ISBNs don't match.
- **Needs a lookup:** books 10007, 10008 and 10009 have invalid ISBNs. Find the real ones.
- **Check this:** all 4 rating updates are 5-star only, which is unusual.
- **Handled automatically, nothing to do:** ISBNs repaired (leading zeros, hyphens, spaces, a lowercase `x`, one moved to the right column), 1 duplicate skipped, *Deathly Hallows*' ISBNs kept instead of being emptied, and book 132's average recalculated from 3.9 to 3.91.

### 4. What changed

Every updated book, with the old and new value of each changed column, so the reader can check them quickly.

### 5. Assumptions and next steps

- **Assumptions:** for example, "batch 2 was treated as newer because neither file has a date." If an assumption is wrong, the numbers are too, so it has to be written down.
- **How to fix a row:** fix it in the source or in a corrections file, then run the load again. Loading twice is safe (Rule 12), so there's no risk of duplicates.
- **Where everything is:** the full problem list as a CSV (`book_id`, title, column, original value, reason, suggested action), plus the input file's name and size and when the load ran.

### What I leave out

No wall of warnings at the top, no error codes without an explanation, and no repeated warnings that don't need any action. Those just get counted in the "handled automatically" group.

My test: if someone reads only the first line at 8 a.m., can they tell whether they need to do anything?

## Questions I still have

- Should I look up ISBNs online to fill in the ones I can't recover?
- Should `inferred` repairs be used in the final data, or only kept for reference?
- How do I find out when each file's data was collected, so I know which one is newer?
- Who reads the load report, and where should it go: a file, an email, or a table in the database?

## Examples

Here's what my rules say should happen to some real and made-up ISBN values. "Original kept" means the value as it arrived is saved in its own column, so nothing is lost.

| # | Column | Value | Where it's from | Result | Cleaned value | Why |
|---|---|---|---|---|---|---|
| 1 | `isbn` | `0439139600` | batch 2, *Goblet of Fire* | ok | `0439139600` | 10 digits and the checksum works (Rule 2) |
| 2 | `isbn` | `043965548X` | `books.csv`, *Prisoner of Azkaban* | ok | `043965548X` | A capital `X` is allowed as the last character (Rule 2) |
| 3 | `isbn` | `439023483` | `books.csv`, *The Hunger Games* | repaired | `0439023483` | Missing leading zero; padded value passes the checksum (Rules 5, 8) |
| 4 | `isbn` | `61120081` | `books.csv`, *To Kill a Mockingbird* | repaired | `0061120081` | Missing two leading zeros (Rules 5, 8) |
| 5 | `isbn` | `0-450-19827-8` | batch 2, *Salt and Ember* | repaired | `0450198278` | Hyphens removed (Rule 5) |
| 6 | `isbn` | `␣162 001 4750␣` | batch 2, *A Field Guide to Quiet Places* | repaired | `1620014750` | Spaces removed (Rule 5) |
| 7 | `isbn` | `031200009x` | batch 2, *Winter Orchard* | repaired | `031200009X` | Lowercase `x` changed to `X` (Rule 5) |
| 8 | `isbn` | `9781982147600` | batch 2, *An Atlas of Small Kindnesses* | repaired, warning | `1982147601`, and `isbn13` = `9781982147600` | An ISBN-13 in the wrong column: move it, then calculate the ISBN-10 (Rule 8) |
| 9 | `isbn` | *(empty)*, with `isbn13` = `9780062398123` | batch 2, *The Cartographer's Error* | repaired | `0062398121` | Calculated from the valid ISBN-13 (Rules 6, 8) |
| 10 | `isbn` | `1416524797` | batch 2, *Paper Moons* | ok, warning | `1416524797` | Valid, but it's also *Angels & Demons*' ISBN, so both rows get a warning (Rule 7) |
| 11 | `isbn` | `385535144` | `books.csv`, *The Racketeer* | rejected | *(empty, original kept)* | Fails the checksum even with the zero added back. The fix is probably `0385535147`, but that's a guess (Rule 8) |
| 12 | `isbn` | `04390A3483` | batch 2, *The Glass Aviary* | rejected | *(empty, original kept)* | A letter in the middle (Rule 6) |
| 13 | `isbn` | `15287463097` | batch 2, *Borrowed Light* | rejected | *(empty, original kept)* | 11 digits; I'd have to guess which one to remove (Rule 6) |
| 14 | `isbn13` | `9798351220413` | batch 2, *Notes from the Night Ferry* | ok | `9798351220413` | Valid `979` ISBN. There's no ISBN-10 to calculate (Rule 3) |
| 15 | `isbn13` | `9.78043902348e+12`, with `isbn` = `439023483` | `books.csv`, *The Hunger Games* | repaired | `9780439023481` | Calculated from the repaired ISBN-10, and it rounds to the stored value (Rule 8) |
| 16 | `isbn13` | `9.78067172365e+12`, no ISBN-10 | `books.csv`, *How to Win Friends and Influence People* | inferred, warning | `9780671723651` | Only one valid ISBN-13 rounds to this value, but that only works if the original was a real ISBN (Rule 8) |
| 17 | `isbn13` | `9.7804251888e+12`, no ISBN-10 | `books.csv`, *Watchers* | rejected | *(empty, original kept)* | Both `9780425188798` and `9780425188804` fit, and picking one would be a coin flip (Rule 8) |
| 18 | `isbn13` | `9.78055381395e+12`, with `isbn` = `446612707` | `books.csv`, *Nights in Rodanthe* | repaired, warning | `9780446612708` | Doesn't match the ISBN-10. Calculated from the ISBN-10; the old number might be another edition (Rule 8) |
| 19 | `isbn13` | `2.94001623192e+12` | `books.csv`, *Wait for You* | moved | `other_id` = `2.94001623192e+12` as stored | A Barnes & Noble ID, not an ISBN. It was rounded too, so its last digit is lost and I don't write it out as if it were exact (Rules 3, 8) |
| 20 | `isbn13` | `76783609419.0`, with `isbn` = `553609416` | `books.csv`, *Anne of Green Gables* box set | moved, then repaired | `other_id` = UPC `076783609419`, `isbn13` = `9780553609417` | A UPC barcode, not an ISBN. The ISBN-13 is calculated from the ISBN-10 (Rule 8) |
| 21 | `isbn13` | `97893806587.0` | `books.csv`, *The Secret of the Nagas* | rejected | *(empty, original kept)* | Cut short to 11 digits, and the ISBN-10 is invalid too, so there's nothing to calculate from (Rule 8) |
| 22 | both | *(empty)* | `books.csv`, *Bossypants* | stays empty | *(empty)* | Missing isn't the same as wrong. The book is still matched by `work_id` (Rules 6, 10) |
| 23 | `isbn13` | `978-0-306-40615-7` | made up | repaired | `9780306406157` | Hyphens removed; valid (Rule 5) |
| 24 | `isbn13` | `9780306406158` | made up | rejected | *(empty, original kept)* | Wrong check digit. It should end in `7` (Rule 8) |
| 25 | `isbn` | `1234567890` | made up | rejected | *(empty, original kept)* | Looks right, but fails the checksum (Rule 2) |
| 26 | `isbn` | `ISBN 0-306-40615-2` | made up | repaired | `0306406152` | The "ISBN" label at the start is removed, like hyphens and spaces (Decision 8) |
| 27 | `isbn13` | `URN:ISBN:978-92-95055-12-4` | ISBN Users' Manual | repaired | `9789295055124` | The "URN:ISBN:" label is removed (Decision 8) |
| 28 | `isbn13` | `ISBN 978-951-45-9693-3 (hardback)` | ISBN Users' Manual | repaired | `9789514596933` | The "ISBN" label and the "(hardback)" format label are removed (Decision 8) |
| 29 | `isbn13` | `9790007672393` | made up | moved | `other_id` = `9790007672393` | An ISMN for printed music, not an ISBN (Rule 3, Decision 12) |
| 30 | `isbn13` | `978-92-95055-12-4 90000` | made up | rejected | *(empty, original kept)* | A five-digit price code isn't removed, so it has 18 digits (Decision 8) |

## Decisions

These answer every question I had before building `ingest.py`. Where a decision and an earlier rule seem to disagree, the decision wins.

### What gets counted

1. **Rejected rows** are rows that don't get written to the database because something is wrong with the whole row: exact duplicates in the same file (Decision 2), CSV rows with the wrong number of fields (Decision 4), and conflicting copies of the same book in one file (Decision 18). A row whose ISBN was rejected still gets loaded, so it isn't a rejected row.
2. **Exact duplicates in the same file** count as rejected, with the reason "exact duplicate of line N". "Skipped because it already exists" only means the book was in the database before this run.
3. **The report** (`report.html`) shows everything from Rule 13 that the log can support: the one-line result, a count that adds up, books before and after, ISBN status counts compared with the previous run, the action groups, what changed, and the assumptions. The problem list is stored in the log, and the report has a "Download as CSV" button instead of the script writing a separate CSV. I'm not building Rule 13's "all updates are 5-star only" check, because it was an observation, not a rule.
4. **A run fails** (and changes nothing) only when the file can't be used at all: it's missing, it isn't UTF-8 text, the CSV can't be parsed, its header is missing any of the 23 expected columns, or the existing log can't be read. Problems in a single row don't fail the run: a row with the wrong number of fields is rejected. Columns other than the ISBNs aren't validated, except that an ID has to be a whole number to be used for matching, and star counts have to be whole numbers to recalculate averages. Each run is all-or-nothing. Failed runs also get a log entry, with status `failed` and the reason.

### Open questions from above

5. **Inferred repairs** fill in the cleaned value with status `inferred` and a warning. Anyone who wants to be strict can leave out `inferred` values.
6. **The newer file** is the one loaded later. The report states this as an assumption.

### Cleaning ISBNs

7. **One value can get several formatting fixes**, always in this order: remove an "ISBN" label (Decision 8), remove a format label at the end (Decision 8), remove spaces and hyphens, change a final `x` to `X`, add back missing leading zeros, then check the checksum. The status is `repaired`.
8. **Labels around the number are removed**, like hyphens and spaces, because the ISBN Users' Manual shows ISBNs written these ways:
    - An "ISBN", "ISBN-10", "ISBN-13" or "URN:ISBN:" label at the start, in any capitalization, with or without a colon (sections 5 and 16.13).
    - A format label in brackets at the end, like "(hardback)" or "(EPUB)" (section 13.1).

    A five-digit price code after an ISBN (section 13.3) is not removed. It only belongs on barcodes, so the value is rejected.
9. **Each ISBN column gets its own status** (`ok`, `repaired`, `inferred`, `rejected`, or `empty`), and each row also gets a summary status: the worse of the two, in the order rejected, inferred, repaired, ok, empty.
10. **Values like `9.78043902348e+12` are rounded to 12 significant digits.** A 13-digit number written this way is the real number rounded to the nearest 10, and a number exactly halfway (ending in 5) goes to the even ten: `…25` becomes `…20` and `…35` becomes `…40`. I checked this against the data: in all 949 rows where the ISBN-10 shows the real number and it ends in 5, it was rounded this way. The conversion uses exact decimal math, never floating point.
11. **Values ending in `.0`** are converted exactly and then sorted:
    - 13 digits: the normal ISBN-13 rules.
    - The same number as the row's ISBN-10: the ISBN-10 in the wrong column. It's kept as the original value, the ISBN-13 is calculated from the ISBN-10, and the row gets a warning.
    - 11 or 12 digits that pass the UPC checksum (after adding back a leading zero): a UPC, moved to `other_id`.
    - Anything else: rejected.
12. **`other_id`** gets every value from the `isbn13` column that isn't an ISBN: 13-digit codes that don't start with `978` or `979`, ISMNs starting with `9790` (Rule 3), and UPCs. Exact values are stored as digits (UPC `076783609419`). Rounded values are stored exactly as they arrived (`2.94001623192e+12`). There's no special rule for placeholder-looking values.
13. **A rejected ISBN-10 is calculated from a valid ISBN-13** in the same row (status `repaired`, with a warning), and the rejected value is kept as the original.
14. **Nothing is calculated from an inferred value.** If the ISBN-13 is inferred, the ISBN-10 stays empty or rejected.
15. **A rounded ISBN-13 can be inferred** whenever there's no usable ISBN-10, whether the ISBN-10 is empty or rejected.
16. **Shared ISBNs** are found by comparing the non-empty cleaned `isbn` and `isbn13` values, both within the file and against the database. A warning can be added to a book that's already in the database.

### Matching and duplicates

17. **An exact duplicate** has identical raw text in every column, in the same file.
18. **The same `work_id` twice in one file with different values:** every copy is rejected, the columns that differ are listed, and it goes under "needs a decision". There's no order inside a file, so keeping either copy would be a guess.
19. **Matching** follows Rule 9. A row is matched on `work_id`. If the `work_id` is new, it's a new book, even if its `goodreads_book_id` or `book_id` belongs to another book, but it gets a warning. If the `work_id` is empty, the row is matched on `goodreads_book_id`, then `book_id`, then the cleaned ISBN, and a match only counts when exactly one book has that value. Those matches also get a warning.
20. **A row that can't be matched and has no `work_id`** is added as a new book, with a "needs a decision" warning that lists any books with the same cleaned title, first author and original publication year (Rule 10).
21. **The database's primary key** is an ID the database makes. `work_id` has to be unique when it's there, but it can be empty.

### Updating books that already exist

22. **Every column has a kind:**
    - IDs (never changed; flagged if different): `work_id`, `goodreads_book_id`, `book_id`, `best_book_id`
    - Descriptive (never replaced with an empty value; a different value is applied and flagged): `title`, `authors`, `original_publication_year`, `original_title`, `language_code`, `image_url`, `small_image_url`, the ISBNs and `other_id`
    - Counts (the newer value is taken; flagged if it goes down): `books_count`, `work_text_reviews_count`, `ratings_count`, `ratings_1` to `ratings_5`. An empty count isn't a newer value, so it doesn't replace a stored one.
    - Calculated (recalculated, never copied): `average_rating`, `work_ratings_count`

    `language_code` values like `eng` and `en-US` aren't standardized, because no rule says to.
23. **`average_rating` and `work_ratings_count` are recalculated for every row on every run.** `work_ratings_count` is the sum of the star counts. `average_rating` is rounded to 2 decimals, with .005 rounding up. If the file's value is different, the recalculated value is stored and the row gets a warning, like "file said 3.9, star counts give 3.91". If a star count is missing or isn't a whole number, the file's values are kept and the row gets a warning.
24. **A different ISBN on a book that's already loaded:** a non-empty cleaned value that's different from the stored one replaces it and is flagged. Its original value and status change with it, and the history keeps the old ones. An empty or rejected incoming value never replaces a stored one. If the cleaned values are the same, nothing changes.
25. **A book counts as "updated"** only when a stored value actually changes, including a recalculated one. A book that only gets new warnings, or whose incoming empty values were ignored, counts as "skipped, already exists".
26. **Every run records which books it saw**, including books that didn't change, so I can tell which files had each book.
27. **The history** records only changes: the run, the book, the column, the old value, the new value and the reason. The record of which run created each book is in Decision 26.

### The log, the report and the files

28. **`ingestion-log.json` is one JSON array.** Each run adds its entry to the end, and earlier entries are never changed. The file is written so that a crash can't leave half a file. If the existing log can't be read or isn't a JSON array, the run stops before touching the database, and that run can't be logged.
29. **Times are stored in UTC** (ISO 8601), and `report.html` shows them in local time. Each entry records the file path as typed, the full path, the file size and a SHA-256 fingerprint of the file.
30. **The log lists rows with warnings** in Rule 13's groups:
    - Needs a decision: ISBNs that don't match, shared ISBNs, ID conflicts, possible title matches, conflicting copies.
    - Needs a lookup: rejected ISBNs that couldn't be calculated from the other column.
    - Check this: inferred values, counts that went down, changed descriptive values, recalculated values that differ from the file, IDs that differ, and other repairs that come with a warning.
    - Handled automatically: only counted, not listed one by one.

    Rows skipped because they already exist are counted, not listed.
31. **`report.html` opens the log with a file picker**, or by dragging the file onto the page, because a page opened from disk can't read files on its own.
32. **The script runs as `python ingest.py <file.csv>`.** `books.db` and `ingestion-log.json` are kept in the same folder as `ingest.py`, wherever the command is run from.
33. **Database names** are chosen by Claude and listed in the "Database layout" section below.

## Database layout

`books.db` has five tables.

**`books`**: one row per book.

| Column | What it holds |
|---|---|
| `book_key` | The database's own ID for the book (Decision 21) |
| `work_id`, `goodreads_book_id`, `best_book_id`, `book_id` | IDs from the file. A whole-number `work_id` has to be unique |
| `isbn`, `isbn13` | The cleaned ISBNs, or empty |
| `isbn_status`, `isbn13_status` | `ok`, `repaired`, `inferred`, `rejected` or `empty` (Decision 9) |
| `isbn_original`, `isbn13_original` | The values exactly as they arrived |
| `other_id` | Store IDs and UPCs found in the `isbn13` column (Decision 12) |
| `isbn_row_status` | The worse of the two ISBN statuses |
| `average_rating`, `work_ratings_count` | Recalculated from the star counts (Decision 23) |
| The other columns from the CSV | Stored as they arrived. Whole-number IDs and counts are stored as numbers |
| `created_run` | The run that added the book |

**`runs`**: one row per successful run, with the same counts as the log. Failed runs change nothing, so they're only in the log.

**`book_sources`**: one row for every book each run saw, with the line number and whether the book was `created`, `updated` or `unchanged` (Decision 26).

**`history`**: one row for every value a run changed: the run, `book_key`, column, old value, new value and reason (Decision 27).

**`warnings`**: every warning from every run, with its group, line, column, value, reason and what to do. Warnings added to a book that was already loaded, like a shared ISBN, are here too (Decision 16).
