from __future__ import annotations

from dataclasses import replace
import tkinter as tk
import unittest
from types import SimpleNamespace

from scpi_automation.app import InstrumentControllerApp
from scpi_automation.identity import (
    ClassificationConfidence,
    DeviceCategory,
    classify_identity,
    parse_idn_response,
    profile_by_id,
)
from scpi_automation.transport import DiscoveryRecord, DiscoveryState
from scpi_automation.ui.category_art import CategoryArtwork, TimelineConnector
from scpi_automation.validation import (
    OperationStatus,
    build_validation_result,
    create_validation_progress,
)


class GuidedDiscoveryUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = InstrumentControllerApp(self.root)
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.app.close()

    def test_initial_screen_uses_beginner_guidance(self) -> None:
        tab = self.app.discovery_tab

        self.assertEqual(tab._active_step, 1)
        self.assertEqual(tab.status_var.get(), "아직 검색을 시작하지 않았어요.")
        self.assertIn("장비 찾아보기", tab.primary_search_button.cget("text"))
        self.assertNotIn("@py", tab.backend_combo.cget("values"))

    def test_direct_fsv30_idn_shows_friendly_classification(self) -> None:
        tab = self.app.discovery_tab
        tab.manual_idn_var.set("Rohde&Schwarz,FSV30,123456,3.50")

        tab.classify_manual_idn()
        self.root.update_idletasks()

        self.assertEqual(len(tab._records), 1)
        self.assertEqual(
            tab._records[0].classification.confidence,
            ClassificationConfidence.EXACT_PROFILE,
        )
        self.assertIn("이름표를 분류했어요", tab.result_title_var.get())
        self.assertIn("실제 장비 연결은 아직", tab.result_body_var.get())

    def test_demo_button_loads_four_clearly_marked_devices(self) -> None:
        tab = self.app.discovery_tab

        tab.show_demo_devices()
        self.root.update_idletasks()

        self.assertTrue(tab._demo_mode)
        self.assertEqual(len(tab._records), 4)
        self.assertTrue(all(record.resource.startswith("DEMO::") for record in tab._records))
        self.assertIn("데모 장비 4대", tab.result_title_var.get())
        self.assertIn("실제 장비와 통신하지 않은", tab.result_body_var.get())

    def test_demo_results_are_grouped_into_category_cards_and_timeline(self) -> None:
        tab = self.app.discovery_tab

        tab.show_demo_devices()
        self.root.update_idletasks()

        def descendants(widget: tk.Misc) -> list[tk.Misc]:
            children = list(widget.winfo_children())
            return children + [
                nested
                for child in children
                for nested in descendants(child)
            ]

        result_widgets = descendants(tab.result_list)
        artworks = [
            widget
            for widget in result_widgets
            if isinstance(widget, CategoryArtwork)
        ]
        connectors = [
            widget
            for widget in result_widgets
            if isinstance(widget, TimelineConnector)
        ]

        self.assertEqual(len(artworks), 4)
        self.assertEqual(
            {artwork.category for artwork in artworks},
            {
                DeviceCategory.SPECTRUM_ANALYZER,
                DeviceCategory.SIGNAL_GENERATOR,
                DeviceCategory.OSCILLOSCOPE,
                DeviceCategory.DIGITAL_MULTIMETER,
            },
        )
        self.assertEqual(len(connectors), 4)

    def test_exact_demo_profiles_skip_confirmation_and_finish_step_four(self) -> None:
        tab = self.app.discovery_tab

        tab.show_demo_devices()
        self.root.update_idletasks()

        self.assertEqual(tab._active_step, 4)
        self.assertTrue(
            all(
                record.classification is not None
                and record.classification.confidence
                == ClassificationConfidence.EXACT_PROFILE
                and record.classification.profile_id
                for record in tab._records
            )
        )
        self.assertEqual(tab.classification_card.winfo_manager(), "")

    def test_unknown_model_requires_operation_validation_result(self) -> None:
        tab = self.app.discovery_tab
        identity = parse_idn_response("Example Instruments,RF-X1,1001,1.0")
        record = DiscoveryRecord(
            resource="TCPIP0::192.0.2.10::INSTR",
            interface="TCPIP0",
            state=DiscoveryState.IDENTIFIED,
            identity=identity,
            classification=classify_identity(identity),
        )
        tab._records = [record]
        tab._reset_routine_selection()
        tab._reset_confirmation_flow()
        tab._direct_input_mode = False
        tab._demo_mode = False

        tab._finish_results(stopped=False)
        self.root.update_idletasks()

        self.assertEqual(tab._active_step, 3)
        self.assertEqual(tab.classification_card.winfo_manager(), "grid")
        self.assertFalse(tab._is_routine_selectable(tab._records[0]))

        tab.confirmation_category_var.set(
            DeviceCategory.SIGNAL_GENERATOR.label_ko
        )
        tab._on_confirmation_category_changed()
        profile_id = tab._profile_display_to_id[
            tab.confirmation_profile_var.get()
        ]
        profile = profile_by_id(profile_id)
        self.assertIsNotNone(profile)
        assert profile is not None
        progress = create_validation_progress(profile, record.resource)
        first, second = progress.operations[:2]
        progress = progress.replace_operation(
            replace(first, status=OperationStatus.PASS)
        )
        progress = progress.replace_operation(
            replace(second, status=OperationStatus.FAIL)
        )
        result_to_apply = build_validation_result(progress)
        tab._apply_validation_result(
            resource=record.resource,
            category=DeviceCategory.SIGNAL_GENERATOR,
            profile=profile,
            result=result_to_apply,
        )
        self.root.update_idletasks()

        result = tab._records[0].classification
        self.assertIsNotNone(result)
        self.assertEqual(
            result.confidence,
            ClassificationConfidence.VALIDATED_PROFILE,
        )
        self.assertEqual(
            result.profile_status,
            "hardware_validated_partial",
        )
        self.assertEqual(
            result.compatible_operation_ids,
            (first.operation_id,),
        )
        self.assertEqual(
            result.incompatible_operation_ids,
            (second.operation_id,),
        )
        self.assertGreater(len(result.unresolved_operation_ids), 0)
        self.assertTrue(tab._is_routine_selectable(tab._records[0]))
        self.assertEqual(tab._active_step, 4)

    def test_rerendered_results_reset_scroll_position_to_top(self) -> None:
        tab = self.app.discovery_tab
        tab.show_demo_devices()
        self.root.update_idletasks()
        tab.result_canvas.yview_moveto(0.75)
        self.root.update_idletasks()

        self.assertGreater(tab.result_canvas.yview()[0], 0.0)

        tab._render_result_cards()
        self.root.update_idletasks()

        self.assertAlmostEqual(tab.result_canvas.yview()[0], 0.0)

    def test_windows_mousewheel_scrolls_results_down(self) -> None:
        tab = self.app.discovery_tab
        tab.show_demo_devices()
        self.root.update_idletasks()
        tab.result_canvas.yview_moveto(0.0)
        before = tab.result_canvas.yview()[0]

        result = tab._on_result_mousewheel(SimpleNamespace(delta=-120))
        self.root.update_idletasks()

        self.assertEqual(result, "break")
        self.assertGreater(tab.result_canvas.yview()[0], before)

    def test_ui_scale_changes_registered_font_sizes(self) -> None:
        tab = self.app.discovery_tab
        font, base_size = next(iter(tab._font_metrics.values()))

        tab.apply_ui_scale(1.25)

        self.assertEqual(tab._ui_scale, 1.25)
        self.assertEqual(
            int(font.cget("size")),
            tab._scaled_size(base_size, 1.25),
        )


if __name__ == "__main__":
    unittest.main()
