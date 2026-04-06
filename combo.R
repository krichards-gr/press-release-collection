library(stringr)
library(lubridate)

#' Extract dates from URL strings
#' @param urls Character vector of URLs
#' @return Date vector (NA where no date found)
#' @examples
#' df %>% mutate(url_date = extract_url_date(url))
extract_url_date <- function(urls) {
  raw <- dplyr::case_when(
    # /YYYY/MM/DD/ or /YYYY/MM/DD- (Walmart, RTX, UnitedHealth, BofA, etc.)
    str_detect(urls, "/\\d{4}/\\d{2}/\\d{2}[/-]") ~
      str_replace(str_extract(urls, "/\\d{4}/\\d{2}/\\d{2}"), "^/", ""),

    # YYYY-MM-DD- at path start (Boeing, Lockheed, AbbVie, Google Cloud, Goldman)
    str_detect(urls, "/\\d{4}-\\d{2}-\\d{2}-") ~
      str_extract(urls, "\\d{4}-\\d{2}-\\d{2}"),

    # trailing -YYYY-MM-DD/ or -YYYY-MM-DD$ (Oracle)
    str_detect(urls, "-\\d{4}-\\d{2}-\\d{2}[/]?$") ~
      str_extract(urls, "\\d{4}-\\d{2}-\\d{2}(?=[/]?$)"),

    # MMDDYYYY--- (Publix: 01282026---publix)
    str_detect(urls, "/\\d{8}---") ~
      str_replace(str_extract(urls, "\\d{8}(?=---)"), "(\\d{2})(\\d{2})(\\d{4})", "\\3/\\1/\\2"),

    # /YYYY/M/ single-digit month (Tyson: /2026/3/)
    str_detect(urls, "/\\d{4}/\\d{1,2}/[^\\d]") ~
      str_replace_all(str_extract(urls, "/\\d{4}/\\d{1,2}/"), "^/|/$", ""),

    # /y2026/m02/ (Cisco)
    str_detect(urls, "/y\\d{4}/m\\d{2}/") ~
      paste0(str_extract(urls, "(?<=y)\\d{4}"), "/",
             str_extract(urls, "(?<=/m)\\d{2}"), "/01"),

    # /2026/m01/ or /2026/m02/ (without y prefix)
    str_detect(urls, "/\\d{4}/m\\d{2}[/]") ~
      paste0(str_extract(urls, "\\d{4}(?=/m\\d{2})"), "/",
             str_extract(urls, "(?<=/m)\\d{2}"), "/01"),

    # /YYYY/MMDD- (ExxonMobil: /2026/0130-)
    str_detect(urls, "/\\d{4}/\\d{4}-") ~
      paste0(str_extract(urls, "(?<=/)\\d{4}(?=/\\d{4})"), "/",
             str_sub(str_extract(urls, "/\\d{4}-"), 2, 3), "/",
             str_sub(str_extract(urls, "/\\d{4}-"), 4, 5)),

    # /YYYY/mon/MMDD slug (GM: /2026/jan/0106- where last 2 digits = day)
    str_detect(urls, "/\\d{4}/(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/\\d{4}") ~
      {
        yr  <- str_extract(urls, "\\d{4}(?=/(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/)")
        dd  <- str_sub(str_extract(urls, "(?<=(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/)\\d{4}"), 3, 4)
        mon <- str_extract(urls, "(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?=/\\d{4})")
        paste0(yr, "-", mon, "-", dd)
      },

    # /YYYY/month/ spelled out (MetLife: /2026/january/)
    str_detect(urls, "/\\d{4}/(january|february|march|april|may|june|july|august|september|october|november|december)/") ~
      paste0(str_extract(urls, "\\d{4}(?=/(january|february|march|april|may|june|july|august|september|october|november|december))"),
             "-", str_extract(urls, "(january|february|march|april|may|june|july|august|september|october|november|december)"),
             "-01"),

    # monDDYY.pdf (Berkshire: feb2826.pdf)
    str_detect(urls, "(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\\d{4}\\.pdf") ~
      {
        chunk <- str_extract(urls, "(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\\d{4}")
        paste0("20", str_sub(chunk, 6, 7), "-", str_sub(chunk, 1, 3), "-", str_sub(chunk, 4, 5))
      },

    # /YYYY/MM/ (Apple: /2026/03/, Facebook: /2026/01/)
    str_detect(urls, "/\\d{4}/\\d{2}/") ~
      paste0(str_extract(urls, "(?<=/)\\d{4}(?=/\\d{2}/)"), "/",
             str_extract(urls, "(?<=\\d{4}/)\\d{2}(?=/)"), "/01"),

    TRUE ~ NA_character_
  )

  lubridate::parse_date_time2(raw, orders = c("Ymd", "Y/m/d", "Ybd")) %>% as.Date()
}




library(stringr)
library(lubridate)

#' Extract the first date from press release article text
#' @param texts Character vector of article text
#' @return Date vector (NA where no date found)
#' @examples
#' df %>% mutate(text_date = extract_text_date(article_text))
extract_text_date <- function(texts) {
  # Full month names: "January 7, 2026" / "March 12, 2026"
  month_full <- "(?:January|February|March|April|May|June|July|August|September|October|November|December)"
  # Abbreviated with optional period: "Jan. 13, 2026" / "Feb 4, 2026"
  month_abbr <- "(?:Jan\\.?|Feb\\.?|Mar\\.?|Apr\\.?|May\\.?|Jun\\.?|Jul\\.?|Aug\\.?|Sep(?:t)?\\.?|Oct\\.?|Nov\\.?|Dec\\.?)"

  # Pattern 1: Month DD, YYYY (full or abbreviated)
  pat_month_day_year <- paste0("(?:", month_full, "|", month_abbr, ")\\s+\\d{1,2},?\\s+\\d{4}")

  # Pattern 2: DD Month YYYY (European style, e.g. "12 March 2026")
  pat_day_month_year <- paste0("\\d{1,2}\\s+(?:", month_full, "|", month_abbr, "),?\\s+\\d{4}")

  # Pattern 3: MM/DD/YYYY
  pat_numeric_slash <- "\\d{1,2}/\\d{1,2}/\\d{4}"

  # Combined pattern — try in order of prevalence
  combined <- paste0("(", pat_month_day_year, "|", pat_day_month_year, "|", pat_numeric_slash, ")")

  raw <- str_extract(texts, combined)

  # Normalize: remove extra dots from abbreviations for parsing
  cleaned <- str_replace_all(raw, "(?<=[A-Za-z])\\.", "")

  lubridate::parse_date_time(cleaned, orders = c("mdY", "mdy", "bdY", "bdy", "dbY", "dby"), quiet = TRUE) %>%
    as.Date()
}


pressers_w_dates <- full_pressers %>%
  dplyr::mutate(
    url_date = extract_url_date(url),
    text_date = extract_text_date(article_text)
  ) %>%
  dplyr::mutate(Date = coalesce(publish_date, url_date, text_date)) %>%
  dplyr::select(-publish_date, -url_date, -text_date)