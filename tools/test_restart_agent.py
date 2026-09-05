import sys
from unittest.mock import MagicMock, patch

# Create a mock for main
main_mock = MagicMock()
main_mock.event_loop_instance = MagicMock()  # not None
main_mock.event_queue = MagicMock()

# Create a mock for memory
memory_mock = MagicMock()
memory_mock.memory_saver = MagicMock()
storage_mock = MagicMock()
memory_mock.memory_saver.storage = storage_mock

# Patch the modules
with patch.dict('sys.modules', {'main': main_mock, 'memory': memory_mock}):
    from tools.restart_agent import restart_agent_tool
    result = restart_agent_tool('')
    # Check that the storage clear was called
    storage_mock.clear.assert_called_once()
    print('Test passed:', result)
