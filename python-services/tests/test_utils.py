"""Unit tests for the matching and geometry helpers."""

from models.models import ImageElement
from utils import bbox_utils
from utils.hash_utils import calculate_phash, calculate_sha256, hash_similarity
from utils.image_matcher import calculate_ssim, describe_image, match_images
from utils import text_matcher as tm


class TestTextNormalization:
    def test_normalizes_typography_and_whitespace(self):
        assert tm.normalize_text('The  “Quick”   Brown') == "the quick brown"

    def test_rejoins_hyphenated_line_breaks(self):
        # pdfplumber returns words split across lines; HTML never does
        assert tm.normalize_text("refrac-\ntion") == "refraction"

    def test_expands_ligatures(self):
        assert tm.normalize_text("ﬁnal") == "final"

    def test_empty_inputs_are_safe(self):
        assert tm.normalize_text(None) == ""
        assert tm.calculate_similarity(None, None) == 1.0
        assert tm.calculate_similarity("a", None) == 0.0


class TestFuzzyMatching:
    def test_reflowed_paragraph_still_matches(self):
        pdf_text = ("The law of reflection states that the angle of incidence\n"
                    "is equal to the angle of reflection.")
        html_text = ("The law of reflection states that the angle of incidence is equal "
                     "to the angle of reflection.")
        assert tm.fuzzy_match(pdf_text, html_text) > 0.95

    def test_unrelated_text_scores_low(self):
        assert tm.fuzzy_match("Photosynthesis in plants", "The capital of France") < 0.5

    def test_best_match_honours_threshold(self):
        candidates = ["alpha beta gamma", "delta epsilon zeta"]
        assert tm.best_match("alpha beta gamma", candidates)[0] == 0
        assert tm.best_match("nothing like it at all", candidates, threshold=0.9)[0] == -1

    def test_match_blocks_reports_both_sides(self):
        result = tm.match_blocks(["one two three", "four five six"], ["one two three"])
        assert result["matches"][0][:2] == (0, 0)
        assert result["unmatched_source"] == [1]
        assert result["unmatched_target"] == []


class TestQuestionParsing:
    def test_parses_number_body_and_options(self):
        parsed = tm.parse_question("3. What is refraction? (a) bending (b) bouncing")
        assert parsed["number"] == "3"
        assert parsed["text"] == "What is refraction?"
        assert parsed["options"] == ["(a) bending", "(b) bouncing"]

    def test_headings_are_not_questions(self):
        assert not tm.looks_like_question("10.1 Reflection of Light")
        assert tm.looks_like_question("2. Define the principal focus.")

    def test_detects_watermark_phrases(self):
        assert tm.detect_watermark_text("Sample copy - do not copy") is not None
        assert tm.detect_watermark_text("The angle of incidence") is None


class TestOrdering:
    def test_identical_order_scores_one(self):
        assert tm.order_similarity([1, 2, 3], [1, 2, 3]) == 1.0

    def test_swapped_elements_lower_the_score(self):
        assert tm.order_similarity([1, 2, 3, 4], [1, 3, 2, 4]) < 1.0


class TestBoundingBoxes:
    def test_iou_and_overlap(self):
        assert bbox_utils.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
        assert bbox_utils.iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
        assert bbox_utils.check_overlap((0, 0, 10, 10), (5, 5, 15, 15))

    def test_normalizes_to_page_relative_coordinates(self):
        box = bbox_utils.normalize_bbox((100, 50, 200, 150), 400, 200)
        assert (box.x0, box.top, box.x1, box.bottom) == (0.25, 0.25, 0.5, 0.75)

    def test_classifies_alignment(self):
        assert bbox_utils.horizontal_alignment((45, 0, 55, 10), 100) == "center"
        assert bbox_utils.horizontal_alignment((2, 0, 30, 10), 100) == "left"
        assert bbox_utils.horizontal_alignment((70, 0, 98, 10), 100) == "right"

    def test_detects_two_columns(self):
        boxes = [(0, 0, 40, 10), (60, 0, 100, 10)]
        assert bbox_utils.detect_columns(boxes, 100) == 2

    def test_merge_and_distance(self):
        merged = bbox_utils.merge_bboxes([(0, 0, 10, 10), (20, 20, 30, 30)])
        assert merged.as_tuple() == (0.0, 0.0, 30.0, 30.0)
        assert bbox_utils.calculate_distance((0, 0, 10, 10), (0, 0, 10, 10)) == 0.0


class TestImageHashing:
    @staticmethod
    def _image(seed=0, size=(120, 90)):
        from PIL import Image

        image = Image.new("RGB", size)
        image.putdata([((x * 7 + seed) % 255, (y * 5) % 255, 90)
                       for y in range(size[1]) for x in range(size[0])])
        return image

    def test_identical_images_match_exactly(self):
        image = self._image()
        assert hash_similarity(calculate_phash(image), calculate_phash(image)) == 1.0
        assert calculate_ssim(image, image) > 0.99

    def test_rescaled_image_still_matches(self):
        image = self._image()
        rescaled = image.resize((60, 45)).resize((120, 90))
        a = ImageElement(**describe_image(image))
        b = ImageElement(**describe_image(rescaled))
        result = match_images([a], [b], 0.75, {a.id: image, b.id: rescaled})
        assert len(result["matches"]) == 1

    def test_different_images_do_not_match(self):
        a_img, b_img = self._image(seed=0), self._image(seed=140, size=(200, 60))
        a = ImageElement(**describe_image(a_img))
        b = ImageElement(**describe_image(b_img))
        result = match_images([a], [b], 0.75, {a.id: a_img, b.id: b_img})
        assert result["matches"] == []
        assert result["unmatched_source"] == [0]

    def test_sha256_of_bytes(self):
        assert calculate_sha256(b"abc").startswith("ba7816bf")
