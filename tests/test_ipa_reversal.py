import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ipa_reversal import (analyze, deserialize, reconstruct, restore_original, reverse_ipa,
                          reverse_ipa_for_db, reverse_tokens, serialize, tokenize_ipa)
from ipa_tokens import PHONEMES


class LosslessTest(unittest.TestCase):
    def check(self, ipa, lang, source, expected):
        tokens = tokenize_ipa(ipa, lang, source)
        self.assertEqual(tokens, tuple(expected))
        self.assertEqual(reconstruct(tokens).encode(), ipa.encode())
        self.assertEqual(reverse_tokens(reverse_tokens(tokens)), tokens)
        key = reverse_ipa_for_db(ipa, lang, source)
        self.assertEqual(deserialize(key), tokens[::-1])
        self.assertEqual(restore_original(str(key)).encode(), ipa.encode())
        self.assertEqual(reverse_ipa(reverse_ipa(ipa, lang, source), lang, source), ipa)
        self.assertFalse(key.startswith(" ") or key.endswith(" ") or "  " in key)

    def test_reference_segmentation(self):
        for lang, ipa, tokens in [
            ("en", "", []), ("en", "t", ["t"]), ("en", "aːb", ["aː", "b"]),
            ("en", "ˈt͡ʃaː", ["ˈt͡ʃ", "aː"]), ("en", "ˈˌkə˞", ["ˈˌk", "ə˞"]),
            ("en", "ããḁ̃ˑ", ["ã", "ã", "ḁ̃ˑ"]), ("en", "tʰtʲkʷlˠ", ["tʰ","tʲ","kʷ","lˠ"]),
            ("en", "ⁿdakʼ", ["ⁿd","a","kʼ"]), ("en", "n̩l̩n̥t̆", ["n̩","l̩","n̥","t̆"]),
            ("en", "aːːtt", ["aːː","t","t"]), ("en", "d̠͡ɹ̠˔", ["d̠͡ɹ̠˔"]),
            ("en", "ˌa͜ɪ͡ə˦˨", ["ˌa͜ɪ͡ə","˦","˨"]), ("en", "aɪ̯a.ɪ", ["aɪ̯","a",".","ɪ"]),
            ("en", "ːaˈ", ["ː","a","ˈ"]), ("en", "☃̃", ["☃̃"]),
            ("en", "[a(b)  t]", ["[","a","(","b",")"," "," ","t","]"]),
            ("en", "/n ̩p⁽ʲ⁾/", ["/","n ̩","p⁽ʲ⁾","/"]),
            ("en", "ˈa\tb\u00a0c", ["ˈa","\t","b","\u00a0","c"]),
            ("en", "t\u200dʃ", ["t\u200dʃ"]), ("en", "t\u200cʃ", ["t","\u200c","ʃ"]),
            ("en", "tstʃ", ["t","s","tʃ"]), ("de", "pfp.ftst.s", ["pf","p",".","f","ts","t",".","s"]),
            ("de", "p͡ft͜sd͡z", ["p͡f","t͜s","d͡z"]), ("de", "aɪaʊɔʏ", ["aɪ","aʊ","ɔʏ"]),
            ("de", "ɡˈast", ["ɡ","ˈa","s","t"]), ("tr", "t͡ʃɯd͜ʒycœ", ["t͡ʃ","ɯ","d͜ʒ","y","c","œ"]),
            ("tr", "aɪʦʧʤ", ["a","ɪ","ʦ","ʧ","ʤ"]), ("tr", "gɡ", ["g","ɡ"]),
        ]:
            with self.subTest(lang=lang, ipa=ipa):
                self.check(ipa, lang, "wiktionary", tokens)
        for vowel in ("aɪ","aʊ","eɪ","oʊ","ɔɪ","ɪə","eə","ʊə"):
            self.check(vowel, "en", "wiktionary", [vowel])
        self.assertEqual(reverse_ipa_for_db("ˈt͡ʃaː", "en"), "aː ˈt͡ʃ")
        self.assertEqual(reverse_ipa_for_db("[a(t)]", "en"), "[ ( t ) a ]")

    def test_source_boundaries_override_inventory(self):
        self.check("ˈa_p_f_ə_l", "de", "espeak", ["ˈa","_","p","_","f","_","ə","_","l"])
        self.check("h_ˈaɪə", "en", "espeak", ["h","_","ˈaɪə"])
        self.check("ˈaɪə", "en", "espeak-separated", ["ˈaɪə"])
        self.check("h\u200cˈaɪə", "en", "espeak", ["h","\u200c","ˈaɪə"])
        self.check("ˈapfəl", "de", "espeak-tied", ["ˈa","p","f","ə","l"])
        self.assertIn(("unknown","_"), analyze("t_s","de").issues)
        self.assertIn(("inferred_multigraph","ts"), analyze("ts","de").issues)
        self.assertIn(("dangling_tie","t͡"), analyze("t͡.","de").issues)
        for transcription in ("t͡ɮ", "ɮ͜a"):
            self.check(transcription, "en", "wiktionary", [transcription])
            self.assertIn(("unknown", "ɮ"), analyze(transcription, "en").issues)
        self.check("ɮz_a", "en", "espeak", ["ɮz", "_", "a"])

    def test_canonical_and_collision_free_wire_format(self):
        tokens = (" ", "\\", r"\u{20}", "\n", "\u2002", "[", "]", "a ̩")
        self.assertEqual(deserialize(serialize(tokens)), tokens)
        for bad in (" ", " a", "a ", "a  b", "\n", r"\q", r"\u{61}", r"\u{000020}"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                deserialize(bad)

    def test_seeded_inventory_sequences(self):
        rng = random.Random(182043)
        for lang, inventory in PHONEMES.items():
            parts = sorted(inventory) + ["ˈ", "ː", "̯", "̥", "̃","\t"," ", "[", ")", "\\","\u200d","☃"]
            for _ in range(750):
                prior = tuple(rng.choices(parts, k=rng.randrange(22)))
                self.assertEqual(deserialize(serialize(reverse_tokens(prior))), prior[::-1])
                ipa = reconstruct(prior)
                self.assertEqual(reconstruct(tokenize_ipa(ipa, lang)), ipa)
                self.assertEqual(reverse_ipa(reverse_ipa(ipa, lang), lang), ipa)
