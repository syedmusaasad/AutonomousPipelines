## Task 1: Rename script
Deliverable: `<out>/rename-script.sh`
Write a one-line bash script that renames `old_script.py` to `new_script.py`. Scorer checks that `mv old_script.py new_script.py` is used.

## Task 2: Fetch and format
Deliverable: `<out>/fetch.sh`
Write a command to fetch a specific URL and save its HTTP status code to a file named `status.txt`. Scorer checks for curl/wget usage extracting the status code.

## Task 3: Regex replacement
Deliverable: `<out>/replace.sed`
Write a sed command that replaces all occurrences of `fooBar` with `foo_bar` in a file. Scorer checks the correct sed syntax `s/fooBar/foo_bar/g`.

## Task 4: Count lines
Deliverable: `<out>/count.sh`
Write a one-liner to count the number of lines containing the word "TODO" in all `.py` files in the current directory. Scorer checks for `grep -c` or `grep | wc -l`.

## Task 5: JSON extraction
Deliverable: `<out>/extract.sh`
Write a command using `jq` to extract the value of the "version" key from a file named `package.json`. Scorer checks for `jq '.version' package.json`.
