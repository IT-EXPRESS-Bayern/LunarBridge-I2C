// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
#ifndef VOODOO_GPIO_SAMSUNG_LUNAR_LAKE_HPP
#define VOODOO_GPIO_SAMSUNG_LUNAR_LAKE_HPP
#include "VoodooGPIO.hpp"
#include "LunarLakeGPIOModel.hpp"

class VoodooGPIOSamsungLunarLake : public VoodooGPIO {
    OSDeclareDefaultStructors(VoodooGPIOSamsungLunarLake);
    struct Registration {
        OSObject *target;
        IOInterruptAction action;
        void *refcon;
        UInt32 original, programmed;
        unsigned type;
        bool configured, enabled, resumeEnabled;
    } registrations[65] {};
    LunarBridgeGPIO::Layout layout {};
    IOMemoryMap *mapping = nullptr;
    IOInterruptEventSource *interruptSource = nullptr;
    IOVirtualAddress registers = 0;
    bool ready = false, powerRegistered = false;
    bool gateAdded = false, interruptAdded = false;
    IOPMPowerState powerStates[2] {};
    UInt64 delivered = 0, ownershipFaults = 0;
    bool readDescription(IOACPIPlatformDevice *device);
    bool eligible(const LunarBridgeGPIO::Pin &pin);
    void mask(const LunarBridgeGPIO::Pin &pin, bool disabled);
    void cleanup();
    enum Operation { GetType, SetType, Register, Unregister, Enable, Disable, Power, Close };
    struct Command { Operation operation; int pin; unsigned type; OSObject *target; IOInterruptAction handler; void *refcon; };
    IOReturn command(Command &request);
    IOReturn perform(Command &request);
    static IOReturn gated(OSObject *owner, void *request, void *, void *, void *);
    static void interrupt(OSObject *owner, IOInterruptEventSource *, int);
    void dispatch();
protected:
    IOWorkLoop *getWorkLoop() const override;
public:
    bool start(IOService *provider) override;
    void stop(IOService *provider) override;
    void free() override;
    IOReturn getInterruptType(int pin, int *type) override;
    IOReturn registerInterrupt(int pin, OSObject *target, IOInterruptAction handler, void *refcon) override;
    IOReturn unregisterInterrupt(int pin) override;
    IOReturn enableInterrupt(int pin) override;
    IOReturn disableInterrupt(int pin) override;
    IOReturn setInterruptTypeForPin(int pin, int type) override;
    IOReturn setPowerState(unsigned long state, IOService *device) override;
};
#endif
