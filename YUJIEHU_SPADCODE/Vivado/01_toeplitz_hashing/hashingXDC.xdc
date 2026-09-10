set_property -dict { PACKAGE_PIN E3 IOSTANDARD LVCMOS33 } [get_ports CLK100MHZ]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports CLK100MHZ]

set_property -dict { PACKAGE_PIN C17 IOSTANDARD LVCMOS33 } [get_ports RAW_RX]

set_property -dict { PACKAGE_PIN D4 IOSTANDARD LVCMOS33 } [get_ports FPGA_TX]