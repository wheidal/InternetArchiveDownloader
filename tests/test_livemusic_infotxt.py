import unittest
from datetime import date

from functools import partial

from iafetch.livemusic.infotxt import normalize_title, parse_info_text as _parse

parse_info_text = partial(_parse, band="Grateful Dead")  # these are the Dead's info files
from iafetch.livemusic.names import (TrackLabel, date_from_name, find_date, name_key, parse_track_label,
                           shnid_from_name, split_track_name)


class NamesTest(unittest.TestCase):
    def test_date_from_name(self):
        self.assertEqual(date_from_name("gd1972-06-17.shure.melton.miller.116272.flac16"), date(1972, 6, 17))
        self.assertEqual(date_from_name("gd75-08-13d1t01"), date(1975, 8, 13))
        self.assertIsNone(date_from_name("shntool"))
        self.assertEqual(date_from_name("jj2008-06-14.mk4"), date(2008, 6, 14))  # other bands play on into the 2000s
        self.assertEqual(date_from_name("tlg06-10-21"), date(2006, 10, 21))

    def test_find_date_forms(self):
        for text, expected in [("June 17, 1972", date(1972, 6, 17)), ("08/13/75", date(1975, 8, 13)),
                               ("1977-05-08", date(1977, 5, 8)), ("Sunday 8th May 1977", date(1977, 5, 8)),
                               ("Sept. 3, 1977", date(1977, 9, 3))]:
            self.assertEqual(find_date(text)[0], expected, text)

    def test_shnid(self):
        self.assertEqual(shnid_from_name("gd72-06-17.116272.txt"), "116272")
        self.assertEqual(shnid_from_name("gd75-08-13.fm.vernon.23661.sbeok.shnf"), "23661")
        self.assertIsNone(shnid_from_name("gd75-08-13.md5"))

    def test_track_labels(self):
        self.assertEqual(parse_track_label("gd72-06-17s1t04"), TrackLabel("s", 1, 4))
        self.assertEqual(parse_track_label("gd75-08-13d2t10"), TrackLabel("d", 2, 10))
        self.assertEqual(parse_track_label("gd77-05-08_07"), TrackLabel("", 1, 7))
        self.assertEqual(parse_track_label("01Tuning"), TrackLabel("", 1, 1))
        self.assertEqual(parse_track_label("GD-11-24-78-D2-T01"), TrackLabel("d", 2, 1))
        self.assertEqual(parse_track_label("gd71-12-31d1track03.wav"), TrackLabel("d", 1, 3))
        self.assertEqual(parse_track_label("gd79-10-27audd3t04"), TrackLabel("d", 3, 4))  # "aud" runs into it
        self.assertEqual(split_track_name("gd1987-04-11t02_Hell In A Bucket"),
                         (TrackLabel("", 1, 2), "Hell In A Bucket"))
        self.assertEqual(split_track_name("02KickTheAmpJam")[1], "Kick The Amp Jam")
        self.assertEqual(parse_track_label("Gd77-05-22D3T6"), TrackLabel("d", 3, 6))
        self.assertIsNone(parse_track_label("gd77-05-08"))


class NormalizeTitleTest(unittest.TestCase):
    def test_markers_and_segues(self):
        self.assertEqual(normalize_title("Bill Graham Intro*>"), ("Bill Graham Intro", True, ["*"], False, None))
        self.assertEqual(normalize_title("It Must Have Been The Roses*;")[2], ["*"])
        self.assertEqual(normalize_title("US Blues * %")[:3], ("US Blues", False, ["*", "%"]))
        self.assertEqual(normalize_title("//GDTRFB* %")[0], "GDTRFB")
        self.assertEqual(normalize_title("Truckin' ->")[:2], ("Truckin'", True))
        self.assertEqual(normalize_title("Slipknot!")[0], "Slipknot!")

    def test_encore_and_length(self):
        self.assertEqual(normalize_title("E: Blues For Allah")[0::3], ("Blues For Allah", True))
        self.assertEqual(normalize_title("Morning Dew [12:04]")[4], "12:04")


FLAC_STYLE = """Grateful Dead - June 17, 1972
Hollywood Bowl - Hollywood, CA

Recording Info:
(FOB) Shure Unidyne III -> Cassette Master (Sony TC-40)

Transfer Info:
Cassette Master -> Sound Devices 744T ->
Adobe Audition v3 -> FLAC/16

All Transfers and Mastering By Charlie Miller

Notes:
-- Recorded By Dave and Jim Melton in Box 52

Set 1:
d1t01 - Tuning
d1t02 - China Cat Sunflower ->
d1t03 - I Know You Rider

Set 2:
d2t01 - Truckin' ->
d2t02 - Drums

SHNTOOL OUTPUT:
    length     expanded size   cdr  WAVE  probs   filename
     2:36.30      27589004     ---   --    ---    gd72-06-17s1t01.wav
"""

SHN_STYLE = """Grateful Dead
Great American Music Hall
San Francisco CA
08/13/75

FM Source:  SBD>FM>MC>DAT>WAV>SHN

Disc 1

Set 1

01. Help On The Way*>
02. Slipknot*>
03. Franklin's Tower* ;

Disc 2

Set 2
01.  Around & Around*;
02   //GDTRFB* %

Encores

03.  US Blues * %

Notes:

*FM broadcast on 9/1/75 - Metromedia Network
d1t09 - cross faded ~ 5 sec patch
"""


class ParseInfoTextTest(unittest.TestCase):
    def test_flac_style_header_and_source(self):
        info = parse_info_text(FLAC_STYLE)
        self.assertEqual((info.band, info.date, info.venue, info.city),
                         ("Grateful Dead", date(1972, 6, 17), "Hollywood Bowl", "Hollywood, CA"))
        self.assertEqual(info.sources, ["AUD"])
        self.assertEqual(info.taper, "Dave and Jim Melton")
        self.assertEqual(info.transferer, "Charlie Miller")
        self.assertIn("Cassette Master -> Sound Devices 744T -> Adobe Audition v3 -> FLAC/16", info.lineage)

    def test_flac_style_setlist(self):
        info = parse_info_text(FLAC_STYLE)
        self.assertEqual([t.title for t in info.titles],
                         ["Tuning", "China Cat Sunflower", "I Know You Rider", "Truckin'", "Drums"])
        self.assertEqual([t.set_name for t in info.titles], ["Set 1"] * 3 + ["Set 2"] * 2)
        self.assertTrue(info.titles[1].segue)
        self.assertEqual(info.titles[3].label, TrackLabel("d", 2, 1))
        self.assertEqual(info.shntool[name_key("gd72-06-17s1t01")].length, "2:36.30")

    def test_shn_style_header(self):
        info = parse_info_text(SHN_STYLE)
        self.assertEqual((info.date, info.venue, info.city),
                         (date(1975, 8, 13), "Great American Music Hall", "San Francisco, CA"))
        self.assertEqual(info.sources, ["FM", "SBD"])

    def test_shn_style_setlist_ignores_notes(self):
        info = parse_info_text(SHN_STYLE)
        self.assertEqual([t.title for t in info.titles],
                         ["Help On The Way", "Slipknot", "Franklin's Tower", "Around & Around", "GDTRFB", "US Blues"])
        self.assertEqual([t.disc for t in info.titles], [1, 1, 1, 2, 2, 2])
        self.assertEqual(info.titles[-1].set_name, "Encore")
        self.assertEqual(info.titles[0].notes, ["FM broadcast on 9/1/75 - Metromedia Network"])

    def test_unnumbered_titles_under_headings(self):
        info = parse_info_text("Grateful Dead\n1977-05-08\nBarton Hall, Ithaca, NY\n\n"
                               "Set 1:\nNew Minglewood Blues\nLoser\n\nSet 2:\nScarlet Begonias ->\nFire On The Mountain\n")
        self.assertEqual(info.city, "Ithaca, NY")
        self.assertEqual(info.venue, "Barton Hall")
        self.assertEqual([t.title for t in info.titles],
                         ["New Minglewood Blues", "Loser", "Scarlet Begonias", "Fire On The Mountain"])
        self.assertTrue(info.titles[2].segue)


# Modeled on gd1977-05-22.31789.txt: no spaces after commas, sets inside disc headings,
# and an unlabeled line inside the numbered list.
LABELED_WITH_GAP = """The Grateful Dead
The Sportatorium,Pembroke Pines,Fla
May 22nd 1977


Disc 3(2nd set/cont)

d3t01 Estimated Prophet>
d3t02 Eyes Of The World>
          Jam>
d3t04 Wharf Rat>

Encore

d3t05 Sugar Magnolia


Some Years Ago WBAI 99.5fm In NYC Aired This Complete Version Of The Show(Unknown Source)
"""

# Modeled on gd1989-06-21.144782.txt
DTS_STYLE = """Grateful Dead
June 21, 1989
Shoreline Amphitheatre,
Mountain View, CA

DTS-Audio-CD 5.1 Mix

Set 2:
24. Turn On Your Lovelight
25. Encore Break
26. Brokedown Palace

Jerry Garcia - Guitar
Bob Weir - Guitar

Recording Info:
SBD -> Cassette Master (TDK MA-XG90)

Patch Info:
Pay Per View Broadcast (shnid=16888) supplies:
Drums (1:05 - 3:07)

All Transfers and Mastering By Charlie Miller

Source Info:
Ted Mattes' Master Audience Dat; Schoeps CMC4/MK2S mics
spaced on 2 stands, OTS > Panasonic SV-250 (16-bit/48k)

Notes:
Mastered by Scott Clugston
- Mixed by kevtobin@yahoo.com

SHNTOOL OUTPUT:
    length     expanded size    cdr  WAVE problems  fmt   ratio  filename
     8:06.03       85698092 B   ---   --   -----    wav  1.0000  24 Turn On Your Lovelight.wav
"""


class NewShowsRegressionTest(unittest.TestCase):
    def test_city_without_spaces_and_old_state_abbreviation(self):
        info = parse_info_text(LABELED_WITH_GAP)
        self.assertEqual((info.band, info.date), ("Grateful Dead", date(1977, 5, 22)))
        self.assertEqual((info.venue, info.city), ("The Sportatorium", "Pembroke Pines, FL"))

    def test_set_named_inside_disc_heading(self):
        info = parse_info_text(LABELED_WITH_GAP)
        self.assertEqual([t.set_name for t in info.titles], ["Set 2", "Set 2", "Set 2", "Encore"])
        self.assertEqual([t.disc for t in info.titles], [3, 3, 3, 3])

    def test_unlabeled_line_between_tracks_is_reported(self):
        info = parse_info_text(LABELED_WITH_GAP)
        self.assertEqual([(u.line_no, u.text) for u in info.unparsed], [(10, "Jam>")])
        self.assertNotIn("Jam", [t.title for t in info.titles])

    def test_fm_written_after_frequency(self):
        self.assertEqual(parse_info_text(LABELED_WITH_GAP).sources, ["FM"])

    def test_dts_sources_skip_patch_section(self):
        info = parse_info_text(DTS_STYLE)
        self.assertEqual(info.sources, ["DTS 5.1", "SBD", "AUD"])  # no FM from the patch's broadcast
        self.assertEqual(info.unparsed, [])  # band members after a blank line aren't tracks

    def test_encore_break_starts_the_encore(self):
        info = parse_info_text(DTS_STYLE)
        self.assertEqual([t.set_name for t in info.titles], ["Set 2", "Set 2", "Encore"])

    def test_people_and_wrapped_lineage(self):
        info = parse_info_text(DTS_STYLE)
        self.assertEqual(info.taper, "Ted Mattes")
        self.assertEqual(info.transferer, "Charlie Miller, Scott Clugston")
        self.assertIn("Ted Mattes' Master Audience Dat; Schoeps CMC4/MK2S mics spaced on 2 stands, "
                      "OTS > Panasonic SV-250 (16-bit/48k)", info.lineage)

    def test_new_shntool_format_with_spaces_in_names(self):
        info = parse_info_text(DTS_STYLE)
        self.assertEqual(info.shntool[name_key("24TurnOnYourLovelight")].length, "8:06.03")


# Modeled on gd82-10-10.txt: the setlist is running text that wraps.
PROSE_SETLIST = """10/10/82 Frost Amphitheater - Palo Alto, CA

Thanks to David Hollister for the seed and transfer

Source:
MC>DAT>ZA2>CD>EAC>SHN

Set 1: Minglewood Blues-> Sugaree-> Little Red Rooster, Tennessee Jed,
Cassidy, Looks Like Rain-> China Cat Sunflower-> I Know
You Rider
Set 2: Playin' In The Band-> Crazy Fingers-> Not
Fade Away-> Sugar Magnolia, E:Satisfaction-> E: Baby Blue

Known Flaws:
> small cut in Crazy Fingers
> the very end of Sunshine Daydream is cut
"""


class ProseSetlistTest(unittest.TestCase):
    def test_titles_and_segues(self):
        info = parse_info_text(PROSE_SETLIST)
        self.assertEqual([t.title for t in info.titles], [
            "Minglewood Blues", "Sugaree", "Little Red Rooster", "Tennessee Jed", "Cassidy", "Looks Like Rain",
            "China Cat Sunflower", "I Know You Rider", "Playin' In The Band", "Crazy Fingers", "Not Fade Away",
            "Sugar Magnolia", "Satisfaction", "Baby Blue"])
        self.assertEqual([t.segue for t in info.titles[:3]], [True, True, False])

    def test_encores_stay_with_their_set(self):
        info = parse_info_text(PROSE_SETLIST)
        self.assertEqual([t.set_name for t in info.titles[-3:]], ["Set 2", "Encore", "Encore"])
        self.assertEqual([t.group for t in info.titles[-3:]], [2, 2, 2])

    def test_bullets_are_not_lineage(self):
        info = parse_info_text(PROSE_SETLIST)
        self.assertEqual(info.lineage, ["MC>DAT>ZA2>CD>EAC>SHN"])
        self.assertEqual(info.transferer, "David Hollister")


class FetchedShowsRegressionTest(unittest.TestCase):
    """Layouts seen in the info files fetched from archive.org."""

    def test_doubled_carriage_returns(self):
        from iafetch.livemusic.names import decode_text
        info = parse_info_text(decode_text(b"Grateful Dead 3/23/87\r\r\nHampton Coliseum, Hampton, VA\r\r\n"))
        self.assertEqual((info.venue, info.city), ("Hampton Coliseum", "Hampton, VA"))

    def test_slash_headings_t_numbers_and_uppercase_encore(self):
        info = parse_info_text("Grateful Dead 3/23/87\nHampton Coliseum, Hampton, VA\n\nSET1 / DISC 1\n"
                               "T01: Crowd/Tuning\nT02: Touch Of Grey >\n\nSET2/ DISC 3\nT01: Space >\n"
                               "T02: ENCORE U.S. Blues\n")
        self.assertEqual([(t.title, t.disc, t.set_name) for t in info.titles],
                         [("Crowd/Tuning", 1, "Set 1"), ("Touch Of Grey", 1, "Set 1"),
                          ("Space", 3, "Set 2"), ("U.S. Blues", 3, "Encore")])

    def test_spaced_date_with_label(self):
        info = parse_info_text("Grateful Dead\nDate 4/ 8/88\nThe Centrum,\nWorcester, MA\n")
        self.assertEqual((info.date, info.venue, info.city), (date(1988, 4, 8), "The Centrum", "Worcester, MA"))

    def test_lowercase_header_and_city_abbreviation(self):
        info = parse_info_text("grateful dead 5/19/66 avalon ballroom, s.f., california\n")
        self.assertEqual((info.venue, info.city), ("Avalon Ballroom", "San Francisco, CA"))

    def test_title_spanning_two_files(self):
        info = parse_info_text("Set 1\n17. good lovin'\n18-19. you don't // have to ask *fade in/out of tape flip\n")
        self.assertEqual([(t.number, t.title) for t in info.titles],
                         [(17, "good lovin'"), (18, "you don't have to ask"), (19, "you don't have to ask")])
        self.assertEqual(info.titles[1].notes, ["fade in/out of tape flip", "part 1 of 2"])

    def test_unnumbered_titles_with_cont_headings_and_missing_encore(self):
        info = parse_info_text("Grateful Dead\n6.20.83\n\nDisc 1\nSet 1\n\nMinglewood Blues\nPeggy-O\n\n"
                               "Disc 2\nSet 1 cont.\n\nThe Music Never Stopped\n\nSet 2\n\nDrums\n\n"
                               "Encore: It's All Over Now Baby Blue (Missing)\n")
        self.assertEqual([(t.title, t.disc, t.set_name) for t in info.titles],
                         [("Minglewood Blues", 1, "Set 1"), ("Peggy-O", 1, "Set 1"),
                          ("The Music Never Stopped", 2, "Set 1"), ("Drums", 2, "Set 2")])
        self.assertEqual([t.title for t in info.missing], ["It's All Over Now Baby Blue"])

    def test_numbers_out_of_sequence_are_not_tracks(self):
        info = parse_info_text("Disc 2:\n1. The Other One -->\n2. Deal\n\n"
                               "updated source info: Sony ECM-22P mics > Sony 770 reel deck\n"
                               "40-50 feet back, dead center; taped by Rob Bertrando et al\n")
        self.assertEqual([t.title for t in info.titles], ["The Other One", "Deal"])
        self.assertEqual(info.ignored[0].line_no, 6)
        self.assertEqual(info.taper, "Rob Bertrando")

    def test_bare_number_under_labeled_setlist_is_ignored(self):
        info = parse_info_text("Set 2\nd2t01 - Tuning\nd2t02 - Bertha ->\nd2t03 - Good Lovin'\n\n"
                               "*Aud recording only\n2 Source Matrix\n")
        self.assertEqual(len(info.titles), 3)
        self.assertEqual(info.footnotes, {"*": "Aud recording only"})

    def test_disc_heading_with_hash_or_length(self):
        info = parse_info_text("Disc #1\n1. Jack Straw\n2. Sugaree\nDisc Two: 60:33\n1. Next Time You See Me\n")
        self.assertEqual([t.disc for t in info.titles], [1, 1, 2])

    def test_single_arrow_outside_source_sections_is_not_lineage(self):
        info = parse_info_text("Grateful Dead\n11/24/78\n\nSource:\nSBD > Reel > DAT\n\n"
                               "Known Flaws - problems during Me & My Uncle > Big River.\n")
        self.assertEqual(info.lineage, ["SBD > Reel > DAT"])

    def test_nak_300_mics_and_taper_at_end_of_source(self):
        info = parse_info_text("The Grateful Dead\nUIC Pavilion\nChicago, IL\nDate 1987-04-11\n\n"
                               "Source:   2x Nak 300 CP4 + 1x Nak 300 (center) > unknown mixer > Nak 550 by Barry Levitt\n")
        self.assertEqual((info.sources, info.taper), (["AUD"], "Barry Levitt"))


# Modeled on gd1989-10-09.89563.txt: an event name above the venue, a "-- encore --" marker line,
# and microphones "near the soundboard".
EVENT_NAME_STYLE = """GRATEFUL DEAD
"Formerly the Warlocks" Concert
Hampton Coliseum
Hampton VA
10-09-1989

Disc Three (49:36)

01 Space >
02 Good Lovin'
-- encore --
03 crowd & tuning
04 Attics of My Life

AUD: Nakamichi CM300's w/CP4 shotguns > Sony TC-D5M > Maxell MX-90's,
with no Dolby. Mics on stand on the floor, near the soundboard.
"""

# Modeled on gd1977-05-09.8036.txt: every header line is labeled.
LABELED_HEADER = """BAND Grateful Dead
VENUE War Memorial
CITY Buffalo
STATE NY
DATE 05/09/77

1. Bertha
"""


class HeaderAndMarkerTest(unittest.TestCase):
    def test_event_name_doesnt_take_the_venue(self):
        info = parse_info_text(EVENT_NAME_STYLE)
        self.assertEqual((info.venue, info.city), ("Hampton Coliseum", "Hampton, VA"))

    def test_dashed_encore_marker(self):
        info = parse_info_text(EVENT_NAME_STYLE)
        self.assertEqual(info.unparsed, [])
        self.assertEqual([t.set_name == "Encore" for t in info.titles], [False, False, True, True])

    def test_mics_near_the_board_is_not_a_soundboard(self):
        self.assertEqual(parse_info_text(EVENT_NAME_STYLE).sources, ["AUD"])
        self.assertEqual(parse_info_text("Grateful Dead\n\nLocation: *OTS at SBD, Stand at 9 Feet\n").sources,
                         ["AUD"])

    def test_labeled_header(self):
        info = parse_info_text(LABELED_HEADER)
        self.assertEqual((info.band, info.venue, info.city, info.date),
                         ("Grateful Dead", "War Memorial", "Buffalo, NY", date(1977, 5, 9)))


# Modeled on gd1979-10-27.19416.txt and gd1973-02-15.info.txt: headings wrapped in dashes, and songs
# the tape missed.
DASHED_HEADINGS = """Grateful Dead
Cape Cod Coliseum
South Yarmouth, MA
October 27, 1979

Disc One
--Set One--
1 Jack Straw
2 Deal

Disc Two
--Set Two begin--
1 Dancing In The Street >
2 El Paso
<missing> Big River
3 Drums >

Disc Three
--Set Two end--
1 Not Fade Away >
-Encore-
2 One More Saturday Night

Taped by Steve Rolfe, 2 Sennheiser 421s > Sony D-5 Location @ Soundboard
"""


class DashedHeadingTest(unittest.TestCase):
    def test_sets_from_dashed_headings(self):
        info = parse_info_text(DASHED_HEADINGS)
        self.assertEqual([(t.disc, t.set_name) for t in info.titles],
                         [(1, "Set 1"), (1, "Set 1"), (2, "Set 2"), (2, "Set 2"), (2, "Set 2"), (3, "Set 2"),
                          (3, "Encore")])
        self.assertEqual(info.unparsed, [])

    def test_missing_line(self):
        info = parse_info_text(DASHED_HEADINGS)
        self.assertEqual([t.title for t in info.missing], ["Big River"])
        self.assertNotIn("Big River", [t.title for t in info.titles])

    def test_at_the_soundboard(self):
        self.assertEqual(parse_info_text(DASHED_HEADINGS).sources, ["AUD"])


# Modeled on gd1973-06-22.txt (an Audience Devotional Tree round): a banner block above the show, lengths
# before the titles, lettered set headings, and "missing:" lines.
ADT_STYLE = """Audience Devotional Tree
Round 19 - April, 2003

GRATEFUL DEAD
Friday, June 22, 1973
P.N.E. Coliseum - Vancouver, BC, Canada
Audience Recording

DISC ONE [75:22]
set one-a:
01 [06:31] Bertha*
missing: China Cat Sunflower >
02 [05:50] I Know You Rider

DISC TWO [68:11]
set one-end:
01 [04:45] Big River
set two-a:
02 [11:39] Here Comes Sunshine
encore:
03 [03:54] Johnny B Goode
"""


class AdtStyleTest(unittest.TestCase):
    def test_banner_above_the_header(self):
        info = parse_info_text(ADT_STYLE)
        self.assertEqual((info.date, info.venue, info.city), (date(1973, 6, 22), "P.N.E. Coliseum", "Vancouver, BC"))

    def test_lengths_first_and_lettered_sets(self):
        info = parse_info_text(ADT_STYLE)
        self.assertEqual([(t.disc, t.set_name, t.title, t.length) for t in info.titles],
                         [(1, "Set 1", "Bertha", "06:31"), (1, "Set 1", "I Know You Rider", "05:50"),
                          (2, "Set 1", "Big River", "04:45"), (2, "Set 2", "Here Comes Sunshine", "11:39"),
                          (2, "Encore", "Johnny B Goode", "03:54")])
        self.assertEqual([t.title for t in info.missing], ["China Cat Sunflower"])


class HeaderBlockTest(unittest.TestCase):
    def test_band_alone_then_date_and_place(self):  # gd1974-08-06.5914.txt
        info = parse_info_text("Audience Devotional Tree Round Two - January 2001\n\nGrateful Dead\n\n"
                               "Tuesday, August 6, 1974\nRoosevelt Stadium - Jersey City, NJ\nAUD / 1\n\n1. Bertha\n")
        self.assertEqual((info.date, info.venue, info.city), (date(1974, 8, 6), "Roosevelt Stadium", "Jersey City, NJ"))

    def test_the_band_beats_a_dated_file_name(self):  # gd1970-11-08.26975.txt
        info = parse_info_text("Ken & Judy Lee Proudly Present:\nThe Port Chester Resurrection Project\n\n"
                               "PCRP#007NRPS-GD1970-11-8.shnf.torrent\n\nNew Riders of The Purple Sage\nGrateful Dead\n"
                               "Capitol Thea., Port Chester NY\nNovember 8th 1970\n")
        self.assertEqual((info.venue, info.city), ("Capitol Thea.", "Port Chester, NY"))

    def test_no_gap_once_the_place_is_known(self):  # gd1980-12-06.151263.txt: the lineage stays lineage
        info = parse_info_text("Grateful Dead\nRecreation Center,\nMill Valley, CA\nSaturday December, 6, 1980\n\n"
                               "AKG C414 Stereo Pair > Sony D5 - Betty Cantor-Jackson's Master Cassette\n")
        self.assertEqual((info.venue, info.city), ("Recreation Center", "Mill Valley, CA"))
        self.assertIn("AUD", info.sources)

    def test_a_sentence_isnt_a_place(self):  # gd1969-03-29.135885.teute.d1t04fix: a patch track's notes
        info = parse_info_text("gd69-03-29d1t04fix\n\n\nd1t04 - Dark Star 21:01\n\n"
                               "This is the complete Dark Star from 1969-03-29. (It is 21 minutes long, while the "
                               "previously circulating version was 15 minutes.)\nThe missing section could come from "
                               "an alternate cassette copy of the master reel, or from an alternate cassette master.\n")
        self.assertEqual((info.date, info.venue, info.city), (date(1969, 3, 29), None, None))


if __name__ == "__main__":
    unittest.main()
