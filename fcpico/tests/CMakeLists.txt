# SPDX-License-Identifier: BSD-3-Clause
#
# fcpico/tests -- ctest C test suites.
#
# Suites are discovered, not listed. Any immediate subdirectory of fcpico/tests/ that
# contains its own CMakeLists.txt is added automatically, in sorted order so the
# configure is deterministic.
#
# This is deliberate. When suites were listed by hand, every issue that added one had to
# edit this single file, so two workers adding a suite at the same time collided on it and
# an issue set that promised "one issue, one worker" could not keep that promise. It also
# meant a listed-but-missing directory failed the configure for everyone, which happened.
# Discovery removes the shared file from every future issue's Owns list: a new suite is
# now a new directory and nothing else.
#
# CONFIGURE_DEPENDS makes the build system re-run the glob when directories appear, so
# adding a suite does not need a manual cmake re-configure.

file(
  GLOB _fcpico_test_suites
  CONFIGURE_DEPENDS
  RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
  "${CMAKE_CURRENT_SOURCE_DIR}/*/CMakeLists.txt"
)
list(SORT _fcpico_test_suites)

foreach(_suite IN LISTS _fcpico_test_suites)
  get_filename_component(_suite_dir "${_suite}" DIRECTORY)
  message(STATUS "fcpico/tests: adding suite ${_suite_dir}")
  add_subdirectory("${_suite_dir}")
endforeach()

if(NOT _fcpico_test_suites)
  message(WARNING "fcpico/tests: no suite directory contains a CMakeLists.txt")
endif()

unset(_fcpico_test_suites)
