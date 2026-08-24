import unittest

from ai_land_design import geometry


class GeometryTest(unittest.TestCase):
    def test_area_and_orientation(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertAlmostEqual(geometry.area(square), 100.0)
        self.assertTrue(geometry.is_ccw(square))
        self.assertFalse(geometry.is_ccw(list(reversed(square))))
        # 向きが逆でも面積は同じ
        self.assertAlmostEqual(geometry.area(list(reversed(square))), 100.0)

    def test_centroid_and_bbox(self):
        rect = geometry.rectangle(2, 3, 4, 6)
        self.assertEqual(geometry.bbox(rect), (2, 3, 6, 9))
        cx, cy = geometry.centroid(rect)
        self.assertAlmostEqual(cx, 4.0)
        self.assertAlmostEqual(cy, 6.0)

    def test_shape_regularity(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        flag = [(0, 0), (16, 0), (16, 9), (4, 9), (4, 14), (0, 14)]
        self.assertAlmostEqual(geometry.shape_regularity(square), 1.0)
        self.assertLess(geometry.shape_regularity(flag), 0.85)

    def test_scale_rect_to_area(self):
        rect = geometry.rectangle(0, 0, 10, 20)
        scaled = geometry.scale_rect_to_area(rect, 50.0)
        self.assertAlmostEqual(geometry.area(scaled), 50.0)
        # 中心は保たれる
        self.assertAlmostEqual(geometry.centroid(scaled)[0], geometry.centroid(rect)[0])
        self.assertAlmostEqual(geometry.centroid(scaled)[1], geometry.centroid(rect)[1])

    def test_scale_rect_does_not_enlarge(self):
        rect = geometry.rectangle(0, 0, 10, 10)
        self.assertAlmostEqual(geometry.area(geometry.scale_rect_to_area(rect, 500.0)), 100.0)

    def test_point_in_polygon(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(geometry.point_in_polygon((5, 5), square))
        self.assertFalse(geometry.point_in_polygon((15, 5), square))


if __name__ == "__main__":
    unittest.main()
