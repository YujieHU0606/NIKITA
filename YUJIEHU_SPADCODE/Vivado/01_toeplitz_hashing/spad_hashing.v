`timescale 1ns / 1ps

module spad_hashing (
    input  wire CLK100MHZ,
    input  wire RAW_RX,
    output wire FPGA_TX
);

    // Short power-on reset
    reg [15:0] reset_count = 16'd0;
    wire rst = (reset_count != 16'hFFFF);

    always @(posedge CLK100MHZ) begin
        if (reset_count != 16'hFFFF)
            reset_count <= reset_count + 1'b1;
    end

    wire [7:0] rx_byte;
    wire rx_valid;

    uart_rx_115200 u_rx (
        .clk(CLK100MHZ),
        .rst(rst),
        .rx(RAW_RX),
        .data_out(rx_byte),
        .data_valid(rx_valid)
    );

    wire [7:0] hash_byte;
    wire hash_valid;
    wire hash_ready;

    toeplitz_1024_512 u_hash (
        .clk(CLK100MHZ),
        .rst(rst),
        .in_byte(rx_byte),
        .in_valid(rx_valid),
        .out_byte(hash_byte),
        .out_valid(hash_valid),
        .out_ready(hash_ready)
    );

    wire tx_busy;
    wire tx_start = hash_valid && !tx_busy;

    assign hash_ready = !tx_busy;

    uart_tx_115200 u_tx (
        .clk(CLK100MHZ),
        .rst(rst),
        .start(tx_start),
        .data_in(hash_byte),
        .tx(FPGA_TX),
        .busy(tx_busy)
    );

endmodule


module toeplitz_1024_512 (
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] in_byte,
    input  wire       in_valid,
    output reg  [7:0] out_byte,
    output wire       out_valid,
    input  wire       out_ready
);

    // Fixed Toeplitz coefficients stored inside the FPGA design.
    // No external coefficient file is required.
    localparam [1535:0] HASH_COEFF = 1536'h7e8460bfbecd3fe04460da38a0723cccee542347e8f14fa4ce29271331373867daac0ba6ce8ab4dee3e2d452a5c2f20e048b7cef97a7e729b54a24123d46108e58866f0a66a3aa3e9a125980ce3bd6d250ba3b652fa7b628a019c68fa927c6d7d9edb616c451a75a99d9449745640015a972baf89517b344a8d980e9866b3f8dc46994d9c7bd462b3306570d04b65346228170f061b8dc90187dfd4e972c7b72d6d501f1a5b5ab424133d34209be203ad8781502d92baeed49ea72fdf360f745;

    wire [1023:0] first_row;

    assign first_row[0] = HASH_COEFF[1535];

    genvar g;
    generate
        for (g = 1; g < 1024; g = g + 1) begin : GEN_FIRST_ROW
            assign first_row[g] = HASH_COEFF[1535 - (511 + g)];
        end
    endgenerate

    reg [1023:0] collect_bits;
    reg [1023:0] pending_bits;
    reg [1023:0] proc_bits;
    reg [1023:0] row_bits;
    reg [511:0] result_bits;

    reg [6:0] input_byte_count;
    reg capture_pending;
    reg pending_valid;

    reg processing;
    reg [8:0] row_index;
    reg [9:0] column_index;
    reg row_parity;

    reg output_active;
    reg [5:0] output_byte_index;

    assign out_valid = output_active;

    wire product_bit =
        proc_bits[column_index] &
        row_bits[column_index];

    integer output_base;

    always @(*) begin
        output_base = output_byte_index * 8;

        out_byte = {
            result_bits[output_base + 0],
            result_bits[output_base + 1],
            result_bits[output_base + 2],
            result_bits[output_base + 3],
            result_bits[output_base + 4],
            result_bits[output_base + 5],
            result_bits[output_base + 6],
            result_bits[output_base + 7]
        };
    end

    always @(posedge clk) begin
        if (rst) begin
            collect_bits <= 1024'd0;
            pending_bits <= 1024'd0;
            proc_bits <= 1024'd0;
            row_bits <= 1024'd0;
            result_bits <= 512'd0;

            input_byte_count <= 7'd0;
            capture_pending <= 1'b0;
            pending_valid <= 1'b0;

            processing <= 1'b0;
            row_index <= 9'd0;
            column_index <= 10'd0;
            row_parity <= 1'b0;

            output_active <= 1'b0;
            output_byte_index <= 6'd0;
        end
        else begin

            // RAW bytes arrive in the same bit order used by the PC script:
            // bit 7 first, then bit 6 ... bit 0.
            if (in_valid) begin
                collect_bits[input_byte_count * 8 + 0] <= in_byte[7];
                collect_bits[input_byte_count * 8 + 1] <= in_byte[6];
                collect_bits[input_byte_count * 8 + 2] <= in_byte[5];
                collect_bits[input_byte_count * 8 + 3] <= in_byte[4];
                collect_bits[input_byte_count * 8 + 4] <= in_byte[3];
                collect_bits[input_byte_count * 8 + 5] <= in_byte[2];
                collect_bits[input_byte_count * 8 + 6] <= in_byte[1];
                collect_bits[input_byte_count * 8 + 7] <= in_byte[0];

                if (input_byte_count == 7'd127) begin
                    input_byte_count <= 7'd0;
                    capture_pending <= 1'b1;
                end
                else begin
                    input_byte_count <= input_byte_count + 1'b1;
                end
            end

            // Copy the completed 1024-bit block one clock later so the
            // last received byte is included.
            if (capture_pending) begin
                capture_pending <= 1'b0;

                if (!pending_valid) begin
                    pending_bits <= collect_bits;
                    pending_valid <= 1'b1;
                end
            end

            // Start one 1024-to-512 Toeplitz block.
            if (pending_valid && !processing && !output_active) begin
                proc_bits <= pending_bits;
                pending_valid <= 1'b0;

                row_bits <= first_row;
                result_bits <= 512'd0;

                row_index <= 9'd0;
                column_index <= 10'd0;
                row_parity <= 1'b0;
                processing <= 1'b1;
            end

            if (processing) begin
                if (column_index == 10'd1023) begin
                    result_bits[row_index] <= row_parity ^ product_bit;
                    column_index <= 10'd0;
                    row_parity <= 1'b0;

                    if (row_index == 9'd511) begin
                        processing <= 1'b0;
                        output_active <= 1'b1;
                        output_byte_index <= 6'd0;
                    end
                    else begin
                        // Each new Toeplitz row is the previous row shifted
                        // by one position with the next first-column value.
                        row_bits <= {
                            row_bits[1022:0],
                            HASH_COEFF[1535 - (row_index + 1'b1)]
                        };
                        row_index <= row_index + 1'b1;
                    end
                end
                else begin
                    row_parity <= row_parity ^ product_bit;
                    column_index <= column_index + 1'b1;
                end
            end

            // 512 output bits are sent as 64 UART bytes.
            if (output_active && out_ready) begin
                if (output_byte_index == 6'd63) begin
                    output_byte_index <= 6'd0;
                    output_active <= 1'b0;
                end
                else begin
                    output_byte_index <= output_byte_index + 1'b1;
                end
            end
        end
    end

endmodule


module uart_rx_115200 (
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,
    output reg  [7:0] data_out,
    output reg        data_valid
);

    localparam integer CLKS_PER_BIT = 868;

    localparam [1:0] RX_IDLE  = 2'd0;
    localparam [1:0] RX_START = 2'd1;
    localparam [1:0] RX_DATA  = 2'd2;
    localparam [1:0] RX_STOP  = 2'd3;

    reg [1:0] state;
    reg [15:0] clock_count;
    reg [2:0] bit_index;
    reg [7:0] rx_buffer;

    always @(posedge clk) begin
        if (rst) begin
            state <= RX_IDLE;
            clock_count <= 16'd0;
            bit_index <= 3'd0;
            rx_buffer <= 8'd0;
            data_out <= 8'd0;
            data_valid <= 1'b0;
        end
        else begin
            data_valid <= 1'b0;

            case (state)
                RX_IDLE: begin
                    clock_count <= 16'd0;
                    bit_index <= 3'd0;

                    if (rx == 1'b0)
                        state <= RX_START;
                end

                RX_START: begin
                    if (clock_count == (CLKS_PER_BIT - 1) / 2) begin
                        if (rx == 1'b0) begin
                            clock_count <= 16'd0;
                            state <= RX_DATA;
                        end
                        else begin
                            state <= RX_IDLE;
                        end
                    end
                    else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end

                RX_DATA: begin
                    if (clock_count == CLKS_PER_BIT - 1) begin
                        clock_count <= 16'd0;
                        rx_buffer[bit_index] <= rx;

                        if (bit_index == 3'd7) begin
                            bit_index <= 3'd0;
                            state <= RX_STOP;
                        end
                        else begin
                            bit_index <= bit_index + 1'b1;
                        end
                    end
                    else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end

                RX_STOP: begin
                    if (clock_count == CLKS_PER_BIT - 1) begin
                        clock_count <= 16'd0;
                        data_out <= rx_buffer;
                        data_valid <= 1'b1;
                        state <= RX_IDLE;
                    end
                    else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end
            endcase
        end
    end

endmodule


module uart_tx_115200 (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data_in,
    output reg        tx,
    output reg        busy
);

    localparam integer CLKS_PER_BIT = 868;

    localparam [1:0] TX_IDLE  = 2'd0;
    localparam [1:0] TX_START = 2'd1;
    localparam [1:0] TX_DATA  = 2'd2;
    localparam [1:0] TX_STOP  = 2'd3;

    reg [1:0] state;
    reg [15:0] clock_count;
    reg [2:0] bit_index;
    reg [7:0] tx_buffer;

    always @(posedge clk) begin
        if (rst) begin
            state <= TX_IDLE;
            clock_count <= 16'd0;
            bit_index <= 3'd0;
            tx_buffer <= 8'd0;
            tx <= 1'b1;
            busy <= 1'b0;
        end
        else begin
            case (state)
                TX_IDLE: begin
                    tx <= 1'b1;
                    busy <= 1'b0;
                    clock_count <= 16'd0;
                    bit_index <= 3'd0;

                    if (start) begin
                        tx_buffer <= data_in;
                        tx <= 1'b0;
                        busy <= 1'b1;
                        state <= TX_START;
                    end
                end

                TX_START: begin
                    if (clock_count == CLKS_PER_BIT - 1) begin
                        clock_count <= 16'd0;
                        tx <= tx_buffer[0];
                        state <= TX_DATA;
                    end
                    else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end

                TX_DATA: begin
                    if (clock_count == CLKS_PER_BIT - 1) begin
                        clock_count <= 16'd0;

                        if (bit_index == 3'd7) begin
                            bit_index <= 3'd0;
                            tx <= 1'b1;
                            state <= TX_STOP;
                        end
                        else begin
                            bit_index <= bit_index + 1'b1;
                            tx <= tx_buffer[bit_index + 1'b1];
                        end
                    end
                    else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end

                TX_STOP: begin
                    if (clock_count == CLKS_PER_BIT - 1) begin
                        clock_count <= 16'd0;
                        tx <= 1'b1;
                        busy <= 1'b0;
                        state <= TX_IDLE;
                    end
                    else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end
            endcase
        end
    end

endmodule
