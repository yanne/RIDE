#  Copyright 2008-2012 Nokia Siemens Networks Oyj
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from time import time
from robotide.context.platform import IS_WINDOWS, IS_MAC
import wx
from wx import stc
from StringIO import StringIO

from robot.parsing.model import TestDataDirectory
from robot.parsing.populators import FromFilePopulator
from robot.parsing.txtreader import TxtReader

from robotide.controller.commands import SetDataFile
from robotide.publish.messages import RideMessage
from robotide.widgets import VerticalSizer, HorizontalSizer, ButtonWithHandler
from robotide.pluginapi import (Plugin, RideSaving, TreeAwarePluginMixin,
        RideTreeSelection, RideNotebookTabChanging, RideDataChanged,
        RideOpenSuite, RideDataChangedToDirty)
from robotide.widgets.text import TextField
from robotide.widgets.label import Label



class TextEditorPlugin(Plugin, TreeAwarePluginMixin):
    title = 'Text Edit'

    def __init__(self, application):
        Plugin.__init__(self, application)
        self._editor_component = None

    @property
    def _editor(self):
        if not self._editor_component:
            self._editor_component = SourceEditor(self.notebook,
                                                  self.title,
                                                  DataValidationHandler(self))
            self._refresh_timer = wx.Timer(self._editor_component)
            self._editor_component.Bind(wx.EVT_TIMER, self._on_timer)
        return self._editor_component

    def enable(self):
        self.add_self_as_tree_aware_plugin()
        self.subscribe(self.OnSaving, RideSaving)
        self.subscribe(self.OnTreeSelection, RideTreeSelection)
        self.subscribe(self.OnDataChanged, RideMessage)
        self.subscribe(self.OnTabChange, RideNotebookTabChanging)
        self._register_shortcuts()
        self._open()

    def _register_shortcuts(self):
        def focused(func):
            def f(event):
                if self.is_focused() and self._editor.is_focused():
                    func(event)
            return f
        self.register_shortcut('CtrlCmd-X', focused(lambda e: self._editor.cut()))
        self.register_shortcut('CtrlCmd-C', focused(lambda e: self._editor.copy()))
        if IS_WINDOWS or IS_MAC: # Linux does not need this key binding
            self.register_shortcut('CtrlCmd-V', focused(lambda e: self._editor.paste()))
        self.register_shortcut('CtrlCmd-Z', focused(lambda e: self._editor.undo()))
        self.register_shortcut('CtrlCmd-Y', focused(lambda e: self._editor.redo()))
        self.register_shortcut('Del', focused(lambda e: self._editor.delete()))
        self.register_shortcut('CtrlCmd-F', lambda e: self._editor._search_field.SetFocus())
        self.register_shortcut('CtrlCmd-G', lambda e: self._editor.OnFind(e))
        self.register_shortcut('CtrlCmd-Shift-G', lambda e: self._editor.OnFindBackwards(e))

    def disable(self):
        self.remove_self_from_tree_aware_plugins()
        self.unsubscribe_all()
        self.unregister_actions()
        self.delete_tab(self._editor)
        self._editor_component = None

    def OnOpen(self, event):
        self._open()

    def _open(self):
        datafile_controller = self.tree.get_selected_datafile_controller()
        if datafile_controller:
            self._open_data_for_controller(datafile_controller)
        self.show_tab(self._editor)

    def OnSaving(self, message):
        if self.is_focused():
            self._editor.save()

    def OnDataChanged(self, message):
        if self._should_process_data_changed_message(message):
            if isinstance(message, RideOpenSuite):
                self._editor.reset()
            if self._editor.dirty:
                self._apply_txt_changes_to_model()
            self._refresh_timer.Start(500, True) # For performance reasons only run after all the data changes

    def _on_timer(self, event):
        self._open_tree_selection_in_editor()
        event.Skip()

    def _should_process_data_changed_message(self, message):
        return isinstance(message, RideDataChanged) and \
               not isinstance(message, RideDataChangedToDirty)

    def OnTreeSelection(self, message):
        if self.is_focused():
            next_datafile_controller = message.item and message.item.datafile_controller
            if self._editor.dirty:
                if not self._apply_txt_changes_to_model():
                    if self._editor.datafile_controller != next_datafile_controller:
                        self.tree.select_controller_node(self._editor.datafile_controller)
                    return
            if next_datafile_controller:
                self._open_data_for_controller(next_datafile_controller)

    def _open_tree_selection_in_editor(self):
        datafile_controller = self.tree.get_selected_datafile_controller()
        if datafile_controller:
            self._editor.open(DataFileWrapper(datafile_controller,
                                              self.global_settings))

    def _open_data_for_controller(self, datafile_controller):
        self._editor.selected(DataFileWrapper(datafile_controller,
                                              self.global_settings))

    def OnTabChange(self, message):
        if message.newtab == self.title:
            self._open()
        elif message.oldtab == self.title:
            self._editor.remove_and_store_state()

    def _apply_txt_changes_to_model(self):
        if not self._editor.save():
            return False
        self._editor.reset()
        return True

    def is_focused(self):
        return self.notebook.current_page_title == self.title


class DataValidationHandler(object):

    def __init__(self, plugin):
        self._plugin = plugin
        self._last_answer = None
        self._last_answer_time = 0

    def set_editor(self, editor):
        self._editor = editor

    def validate_and_update(self, data, text):
        if not self._sanity_check(data, text):
            self._handle_sanity_check_failure()
            return False
        else:
            self._editor.reset()
            data.update_from(text)
            return True

    def _sanity_check(self, data, text):
        formatted_text = data.format_text(text).encode('UTF-8')
        c = self._remove_all(formatted_text, ' ', '\n', '...', '\r', '*')
        e = self._remove_all(text, ' ', '\n', '...', '\r', '*')
        return len(c) == len(e)

    def _remove_all(self, original_txt, *to_remove):
        txt = original_txt
        for item in to_remove:
            txt = txt.replace(item, '')
        return txt

    def _handle_sanity_check_failure(self):
        if self._last_answer == wx.ID_NO and \
            time() - self._last_answer_time <= 0.2:
            self._editor._mark_file_dirty()
            return
        # TODO: use widgets.Dialog
        id = wx.MessageDialog(self._editor,
                         'ERROR: Data sanity check failed!\n'\
                         'Reset changes?',
                         'Can not apply changes from Txt Editor',
                          style=wx.YES|wx.NO).ShowModal()
        self._last_answer = id
        self._last_answer_time = time()
        if id == wx.ID_NO:
            self._editor._mark_file_dirty()
        else:
            self._editor._revert()


class DataFileWrapper(object): # TODO: bad class name

    def __init__(self, data, settings):
        self._data = data
        self._settings = settings

    def __eq__(self, other):
        if other is None:
            return False
        return self._data == other._data

    def update_from(self, content):
        self._data.execute(SetDataFile(self._create_target_from(content)))

    def _create_target_from(self, content):
        src = StringIO(content)
        target = self._create_target()
        FromStringIOPopulator(target).populate(src)
        return target

    def format_text(self, text):
        return self._txt_data(self._create_target_from(text))

    def mark_data_dirty(self):
        self._data.mark_dirty()

    def _create_target(self):
        target_class = type(self._data.data)
        if target_class is TestDataDirectory:
            target = TestDataDirectory(source=self._data.directory)
            target.initfile = self._data.data.initfile
            return target
        return target_class(source=self._data.source)

    @property
    def content(self):
        return self._txt_data(self._data.data)

    def _txt_data(self, data):
        output = StringIO()
        data.save(output=output, format='txt',
                  txt_separating_spaces=self._settings['txt number of spaces'])
        return output.getvalue().decode('UTF-8')


class SourceEditor(wx.Panel):

    def __init__(self, parent, title, data_validator):
        wx.Panel.__init__(self, parent)
        self._data_validator = data_validator
        self._data_validator.set_editor(self)
        self._parent = parent
        self._create_ui(title)
        self._data = None
        self._dirty = False

    def is_focused(self):
        foc = wx.Window.FindFocus()
        return any(elem == foc for elem in [self]+list(self.GetChildren()))

    def _create_ui(self, title):
        self.SetSizer(VerticalSizer())
        self._create_editor_toolbar()
        self._create_editor_text_control()
        self._parent.add_tab(self, title, allow_closing=False)

    def _create_editor_toolbar(self):
        editor_toolbar_sizer = HorizontalSizer()
        editor_toolbar_sizer.add_with_padding(
            ButtonWithHandler(self, 'Apply Changes',
                handler=lambda e: self.save()))
        self._create_search(editor_toolbar_sizer)
        self.Sizer.add(editor_toolbar_sizer)

    def _create_search(self, editor_toolbar_sizer):
        editor_toolbar_sizer.AddSpacer(20)
        self._search_field = TextField(self, '', process_enters=True)
        self._search_field.Bind(wx.EVT_TEXT_ENTER, self.OnFind)
        editor_toolbar_sizer.add_with_padding(self._search_field)
        editor_toolbar_sizer.add_with_padding(
            ButtonWithHandler(self, 'Search', handler=self.OnFind))
        self._search_field_notification = Label(self, label='')
        editor_toolbar_sizer.add_with_padding(self._search_field_notification)

    @property
    def dirty(self):
        return self._dirty

    @property
    def datafile_controller(self):
        return self._data._data

    def OnFind(self, event):
        self._find()

    def OnFindBackwards(self, event):
        self._find(forward=False)

    def _find(self, forward=True):
        txt = self._search_field.GetValue()
        position = self._find_text_position(forward, txt)
        self._show_search_results(position, txt)

    # FIXME: This must be cleaned up
    def _find_text_position(self, forward, txt):
        file_end = len(self._editor.utf8_text)
        search_end = file_end if forward else 0
        anchor = self._editor.GetAnchor()
        anchor += 1 if forward else 0
        position = self._editor.FindText(anchor, search_end, txt, 0)
        if position == -1:
            start, end = (0, file_end) if forward else (file_end - 1, 0)
            position = self._editor.FindText(start, end, txt, 0)
        return position

    def _show_search_results(self, position, txt):
        if position != -1:
            self._editor.SetSelection(position, position + len(txt))
            self._search_field_notification.SetLabel('')
        else:
            self._search_field_notification.SetLabel('No matches found.')

    def open(self, data):
        self.reset()
        self._data = data
        if not self._editor:
            self._stored_text = self._data.content
        else:
            self._editor.set_text(self._data.content)

    def selected(self, data):
        if not self._editor:
            self._create_editor_text_control(self._stored_text)
        if self._data == data:
            return
        self.open(data)

    def reset(self):
        self._dirty = False

    def save(self, *args):
        if self.dirty:
            if not self._data_validator.validate_and_update(self._data,
                                                     self._editor.utf8_text):
                return False
        return True

    def delete(self):
        if self._editor.GetSelectionStart() == self._editor.GetSelectionEnd():
            self._editor.CharRight()
        self._editor.DeleteBack()

    def cut(self):
        self._editor.Cut()

    def copy(self):
        self._editor.Copy()

    def paste(self):
        focus = wx.Window.FindFocus()
        if focus == self._editor:
            self._editor.Paste()
        elif focus == self._search_field:
            self._search_field.Paste()

    def undo(self):
        self._editor.Undo()

    def redo(self):
        self._editor.Redo()

    def remove_and_store_state(self):
        self._stored_text = self._editor.GetText()
        self._editor.Destroy()
        self._editor = None

    def _create_editor_text_control(self, text=None):
        self._editor = RobotDataEditor(self)
        self.Sizer.add_expanding(self._editor)
        self.Sizer.Layout()
        if text is not None:
            self._editor.set_text(text)
        self._editor.Bind(wx.EVT_KEY_UP, self.OnEditorKey)
        self._editor.Bind(wx.EVT_KILL_FOCUS, self.LeaveFocus)
        self._editor.Bind(wx.EVT_SET_FOCUS, self.GetFocus)

    def LeaveFocus(self, event):
        self._editor.SetCaretPeriod(0)
        self.save()

    def GetFocus(self, event):
        self._editor.SetCaretPeriod(500)
        event.Skip()

    def _revert(self):
        self.reset()
        self._editor.set_text(self._data.content)

    def OnEditorKey(self, event):
        if not self.dirty and self._editor.GetModify():
            self._mark_file_dirty()
        event.Skip()

    def _mark_file_dirty(self):
        if self._data:
            self._dirty = True
            self._data.mark_data_dirty()


class RobotDataEditor(stc.StyledTextCtrl):

    def __init__(self, parent):
        stc.StyledTextCtrl.__init__(self, parent)
        self.StyleSetSpec(stc.STC_STYLE_DEFAULT, 'fore:#000000,back:#FFFFFF,face:Courier New,size:12')
        self.SetMarginType(0, stc.STC_MARGIN_NUMBER)
        self.SetMarginWidth(0, self.TextWidth(stc.STC_STYLE_LINENUMBER,'1234'))
        self.SetReadOnly(True)

    def set_text(self, text):
        self.SetReadOnly(False)
        self.SetText(text)
        self.EmptyUndoBuffer()

    @property
    def utf8_text(self):
        return self.GetText().encode('UTF-8')


class FromStringIOPopulator(FromFilePopulator):

    def populate(self, content):
        TxtReader().read(content, self)
