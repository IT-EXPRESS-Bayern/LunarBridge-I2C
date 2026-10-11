// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
// Experimental GPI1 port. ACPI/firmware ownership is never taken by this driver.
#include "VoodooGPIOSamsungLunarLake.hpp"
#include <IOKit/pci/IOPCIDevice.h>
#include <libkern/c++/OSBoolean.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSString.h>

OSDefineMetaClassAndStructors(VoodooGPIOSamsungLunarLake, VoodooGPIO);

namespace {
// Keep the active source alive even if its client removes it reentrantly.
// The base checkForWork still reads source state after calling the action.
class LunarBridgeGPIOInterruptSource : public IOInterruptEventSource {
    OSDeclareDefaultStructors(LunarBridgeGPIOInterruptSource);
public:
    static LunarBridgeGPIOInterruptSource *create(OSObject *client, Action handler, IOService *provider) {
        LunarBridgeGPIOInterruptSource *source = new LunarBridgeGPIOInterruptSource;
        if (source && !source->init(client, handler, provider, 0)) {
            source->release();
            return nullptr;
        }
        return source;
    }
    bool checkForWork() override {
        retain();
        const bool more = IOInterruptEventSource::checkForWork();
        release();
        return more;
    }
};
OSDefineMetaClassAndStructors(LunarBridgeGPIOInterruptSource, IOInterruptEventSource);

const UInt8 propertiesUUID[16] = {0x14,0xD8,0xFF,0xDA,0xBA,0x6E,0x8C,0x4D,0x8A,0x91,0xBC,0x9B,0xBF,0x4A,0xA3,0x01};
const UInt8 hierarchyUUID[16] = {0xE6,0xE3,0xB8,0xDB,0x86,0x58,0xA6,0x4B,0x87,0x95,0x13,0x19,0xF5,0x2A,0x96,0x6B};
OSArray *section(OSObject *object, const UInt8 *identifier) {
    OSArray *array = OSDynamicCast(OSArray, object), *found = nullptr;
    if (!array || array->getCount() > 8 || (array->getCount() & 1)) return nullptr;
    for (unsigned index = 0; index < array->getCount(); index += 2) {
        OSData *uuid = OSDynamicCast(OSData, array->getObject(index));
        if (!uuid || uuid->getLength() != 16) return nullptr;
        if (!memcmp(uuid->getBytesNoCopy(), identifier, 16)) {
            if (found) return nullptr;
            found = OSDynamicCast(OSArray, array->getObject(index + 1));
            if (!found || found->getCount() > 32) return nullptr;
        }
    }
    return found;
}
OSObject *property(OSArray *array, const char *name) {
    if (!array) return nullptr;
    OSObject *found = nullptr;
    for (unsigned index = 0; index < array->getCount(); ++index) {
        OSArray *pair = OSDynamicCast(OSArray, array->getObject(index));
        if (!pair || pair->getCount() != 2) return nullptr;
        OSString *key = OSDynamicCast(OSString, pair->getObject(0));
        if (!key) return nullptr;
        if (key->isEqualTo(name)) {
            if (found) return nullptr;
            found = pair->getObject(1);
        }
    }
    return found;
}
bool number(OSArray *array, const char *key, uint32_t &value) {
    OSNumber *item = OSDynamicCast(OSNumber, property(array, key));
    if (!item || item->unsigned64BitValue() > 0xFFFFFFFFULL) return false;
    value = item->unsigned32BitValue();
    return true;
}
bool string(OSArray *array, const char *key, const char *value) {
    OSString *item = OSDynamicCast(OSString, property(array, key));
    return item && item->isEqualTo(value);
}
bool samsungControllerPresent() {
    OSDictionary *matching = IOService::serviceMatching("IOPCIDevice");
    if (!matching) return false;
    OSIterator *iterator = IOService::getMatchingServices(matching);
    matching->release();
    if (!iterator) return false;
    bool found = false;
    while (OSObject *object = iterator->getNextObject()) {
        IOPCIDevice *pci = OSDynamicCast(IOPCIDevice, object);
        if (pci && LunarBridgeGPIO::isSamsung(pci->configRead16(0), pci->configRead16(2),
                                             pci->configRead16(0x2C), pci->configRead16(0x2E))) {
            found = true;
            break;
        }
    }
    iterator->release();
    return found;
}
}

bool VoodooGPIOSamsungLunarLake::readDescription(IOACPIPlatformDevice *device) {
    UInt32 uid = 0;
    OSObject *object = nullptr;
    if (device->evaluateInteger("_UID", &uid) != kIOReturnSuccess || uid != 1 ||
        device->evaluateObject("_CID", &object) != kIOReturnSuccess) return false;
    OSString *cid = OSDynamicCast(OSString, object);
    bool correctID = cid && cid->isEqualTo("INTC105F");
    OSSafeReleaseNULL(object);
    if (!correctID || device->evaluateObject("_DSD", &object) != kIOReturnSuccess) return false;
    OSArray *values = section(object, propertiesUUID), *links = section(object, hierarchyUUID);
    bool valid = string(values, "intc-gpio-community-name", "Community1") &&
        number(values, "intc-gpio-sw-revision", layout.revision) &&
        number(values, "intc-gpio-group-count", layout.groupCount) &&
        number(values, "intc-gpio-group-stride", layout.stride) &&
        number(values, "intc-gpio-pad-ownership-offset", layout.padOwnership) &&
        number(values, "intc-gpio-pad-configuration-lock-offset", layout.configurationLock) &&
        number(values, "intc-gpio-host-software-pad-ownership-offset", layout.hostOwnership) &&
        number(values, "intc-gpio-gpi-interrupt-status-offset", layout.interruptStatus) &&
        number(values, "intc-gpio-gpi-interrupt-enable-offset", layout.interruptEnable) &&
        links && links->getCount() == 3 && layout.groupCount == 3;
    const char *keys[3] = {"intc-gpio-group-0-subproperties", "intc-gpio-group-1-subproperties", "intc-gpio-group-2-subproperties"};
    const char *objects[3] = {"GPPF", "GPPE", "JTAG"}, *names[3] = {"GPP_F", "GPP_E", "CPUJTAG"};
    for (unsigned index = 0; valid && index < 3; ++index) {
        valid = string(links, keys[index], objects[index]);
        OSObject *group = nullptr;
        if (valid && device->evaluateObject(objects[index], &group) == kIOReturnSuccess) {
            OSArray *groupValues = section(group, propertiesUUID);
            valid = string(groupValues, "intc-gpio-group-name", names[index]) &&
                number(groupValues, "intc-gpio-pad-count", layout.groups[index].pads) &&
                number(groupValues, "intc-gpio-group-offset", layout.groups[index].configuration);
        } else valid = false;
        OSSafeReleaseNULL(group);
    }
    OSSafeReleaseNULL(object);
    return valid && LunarBridgeGPIO::valid(layout, 0x10000);
}

IOWorkLoop *VoodooGPIOSamsungLunarLake::getWorkLoop() const { return workLoop; }

bool VoodooGPIOSamsungLunarLake::start(IOService *provider) {
    // The opt-in is deliberately checked before ACPI evaluation, MMIO mapping or IRQ registration.
    if (getProperty("LunarBridgeGPIOEnabled") != kOSBooleanTrue) {
        IOLog("LunarBridgeGPIO: experimental GPI1 path disabled\n");
        return false;
    }
    IOACPIPlatformDevice *device = OSDynamicCast(IOACPIPlatformDevice, provider);
    if (!device || !samsungControllerPresent() || !readDescription(device) ||
        provider->getDeviceMemoryCount() != 1 || !IOService::start(provider)) return false;
    mapping = provider->mapDeviceMemoryWithIndex(0);
    if (!mapping || !LunarBridgeGPIO::valid(layout, mapping->getLength())) goto failure;
    registers = mapping->getVirtualAddress();
    if (!registers || (registers & 3U) || readl(registers + 0x0C) != layout.groups[0].configuration) goto failure;
    workLoop = IOWorkLoop::workLoop();
    if (!workLoop) goto failure;
    command_gate = IOCommandGate::commandGate(this);
    if (!command_gate || workLoop->addEventSource(command_gate) != kIOReturnSuccess) goto failure;
    gateAdded = true;
    interruptSource = LunarBridgeGPIOInterruptSource::create(this, interrupt, provider);
    if (!interruptSource) goto failure;
    interruptSource->disable();
    if (workLoop->addEventSource(interruptSource) != kIOReturnSuccess) goto failure;
    interruptAdded = true;
    controllerIsAwake = true;
    ready = true;
    setProperty("LunarBridgeGPIOResourceSource", "\\_SB.GPI1");
    setProperty("LunarBridgeGPIOExperimental", true);
    setProperty("LunarBridgeGPIOPins", 65, 32);
    setProperty("LunarBridgeGPIORevision", readl(registers) >> 16, 32);
    powerStates[0].version = powerStates[1].version = 1;
    powerStates[1].capabilityFlags = powerStates[1].outputPowerCharacter = powerStates[1].inputPowerRequirement = kIOPMPowerOn;
    PMinit();
    powerRegistered = true;
    provider->joinPMtree(this);
    if (registerPowerDriver(this, powerStates, 2) != kIOReturnSuccess) goto failure;
    IOLog("LunarBridgeGPIO: experimental GPI1 ready; no pins changed during startup\n");
    registerService();
    return true;
failure:
    cleanup();
    IOService::stop(provider);
    return false;
}

bool VoodooGPIOSamsungLunarLake::eligible(const LunarBridgeGPIO::Pin &pin) {
    // No ownership or lock register is ever written. Refuse native mux/APIC/SCI/SMI/NMI routing and disabled input.
    return LunarBridgeGPIO::eligible(pin, readl(registers + pin.padOwnership), readl(registers + pin.hostOwnership),
                                   readl(registers + pin.configurationLock), readl(registers + pin.configuration));
}
void VoodooGPIOSamsungLunarLake::mask(const LunarBridgeGPIO::Pin &pin, bool disabled) {
    const UInt32 bit = UInt32(1) << pin.bit, value = readl(registers + pin.interruptEnable);
    writel(disabled ? value & ~bit : value | bit, registers + pin.interruptEnable);
}
IOReturn VoodooGPIOSamsungLunarLake::gated(OSObject *owner, void *request, void *, void *, void *) {
    return static_cast<VoodooGPIOSamsungLunarLake *>(owner)->perform(*static_cast<Command *>(request));
}
IOReturn VoodooGPIOSamsungLunarLake::command(Command &request) {
    return command_gate && gateAdded ? command_gate->runAction(gated, &request) : kIOReturnNotReady;
}
IOReturn VoodooGPIOSamsungLunarLake::perform(Command &request) {
    if (request.operation == Close) {
        ready = false;
        setProperty("LunarBridgeGPIOIRQCallbacks", delivered, 64);
        setProperty("LunarBridgeGPIOOwnershipFaults", ownershipFaults, 64);
        if (interruptSource) interruptSource->disable();
        for (unsigned index = 0; index < 65; ++index) {
            LunarBridgeGPIO::Pin pin;
            Registration &entry = registrations[index];
            if (entry.target && controllerIsAwake && registers && LunarBridgeGPIO::pin(layout, index, mapping->getLength(), pin) && eligible(pin)) {
                mask(pin, true);
                // Restore only our unchanged programming while ownership and unlock checks still pass.
                if (entry.configured && eligible(pin) && readl(registers + pin.configuration) == entry.programmed)
                    writel(entry.original, registers + pin.configuration);
            }
            OSSafeReleaseNULL(entry.target);
            entry = {};
        }
        return kIOReturnSuccess;
    }
    if (!ready) return kIOReturnNotReady;
    if (request.operation == Power) {
        if (!request.type) {
            if (interruptSource) interruptSource->disable();
            for (unsigned index = 0; index < 65; ++index) {
                Registration &entry = registrations[index];
                entry.resumeEnabled = entry.enabled;
                LunarBridgeGPIO::Pin pin;
                if (entry.enabled && LunarBridgeGPIO::pin(layout, index, mapping->getLength(), pin) && eligible(pin)) mask(pin, true);
                entry.enabled = false;
            }
            controllerIsAwake = false;
        } else if (!controllerIsAwake) {
            if (readl(registers + 0x0C) != layout.groups[0].configuration) return kIOReturnNotPermitted;
            controllerIsAwake = true;
            for (unsigned index = 0; index < 65; ++index) {
                Registration &entry = registrations[index];
                if (entry.resumeEnabled) {
                    Command enable {Enable, static_cast<int>(index), 0, nullptr, nullptr, nullptr};
                    if (perform(enable) != kIOReturnSuccess) IOLog("LunarBridgeGPIO: resume refused pin %u\n", index);
                    entry.resumeEnabled = false;
                }
            }
        }
        return kIOReturnSuccess;
    }
    LunarBridgeGPIO::Pin pin;
    // CPUJTAG is described for bounds/profile verification and never exported as an IRQ.
    if (!LunarBridgeGPIO::pin(layout, request.pin, mapping->getLength(), pin) || pin.group == 2) return kIOReturnNoInterrupt;
    // IOKit delivery type follows the parent source; Linux IRQ_TYPE values only program the pad.
    if (request.operation == GetType)
        return request.refcon ? getProvider()->getInterruptType(0, static_cast<int *>(request.refcon)) : kIOReturnBadArgument;
    Registration &entry = registrations[request.pin];
    if (request.operation == SetType) {
        UInt32 ignored = 0;
        if (entry.enabled || !LunarBridgeGPIO::irqConfiguration(0, request.type, ignored)) return kIOReturnBadArgument;
        entry.type = request.type;
        return kIOReturnSuccess;
    }
    if (request.operation == Register) {
        if (!request.target || !request.handler || entry.target || !entry.type || !controllerIsAwake || !eligible(pin)) return kIOReturnNotPermitted;
        // Do not take an IRQ bit already enabled by firmware or another consumer.
        if (readl(registers + pin.interruptEnable) & (UInt32(1) << pin.bit)) return kIOReturnExclusiveAccess;
        request.target->retain();
        entry.target = request.target;
        entry.action = request.handler;
        entry.refcon = request.refcon;
        entry.original = readl(registers + pin.configuration);
        setProperty("LunarBridgeGPIOLastPin", static_cast<UInt32>(request.pin), 32);
        setProperty("LunarBridgeGPIOLastType", entry.type, 32);
        IOLog("LunarBridgeGPIO: registered GPI1 pin %d type %u\n", request.pin, entry.type);
        return kIOReturnSuccess;
    }
    if (!entry.target) return kIOReturnNoInterrupt;
    if (request.operation == Unregister) {
        if (controllerIsAwake && eligible(pin)) {
            mask(pin, true);
            if (entry.configured && eligible(pin) && readl(registers + pin.configuration) == entry.programmed)
                writel(entry.original, registers + pin.configuration);
        }
        OSSafeReleaseNULL(entry.target);
        entry = {};
    } else if (request.operation == Enable) {
        if (!controllerIsAwake || !eligible(pin)) return kIOReturnNotPermitted;
        UInt32 configured;
        const UInt32 current = readl(registers + pin.configuration);
        if (entry.configured && current != entry.programmed && current != entry.original) return kIOReturnNotPermitted;
        if (!entry.configured) entry.original = current;
        if (!LunarBridgeGPIO::irqConfiguration(current, entry.type, configured)) return kIOReturnBadArgument;
        mask(pin, true);
        // Track attempted programming before readback; cleanup restores only an exact owned value.
        entry.programmed = configured;
        entry.configured = true;
        writel(configured, registers + pin.configuration);
        if (readl(registers + pin.configuration) != configured) return kIOReturnIOError;
        writel(UInt32(1) << pin.bit, registers + pin.interruptStatus);
        entry.enabled = true;
        mask(pin, false);
        if (!(readl(registers + pin.interruptEnable) & (UInt32(1) << pin.bit))) {
            entry.enabled = false;
            return kIOReturnIOError;
        }
        interruptSource->enable();
    } else if (request.operation == Disable) {
        if (controllerIsAwake && eligible(pin)) mask(pin, true);
        entry.enabled = entry.resumeEnabled = false;
    } else return kIOReturnBadArgument;
    bool any = false;
    for (unsigned index = 0; index < 65; ++index) any |= registrations[index].enabled;
    if (!any) interruptSource->disable();
    return kIOReturnSuccess;
}

void VoodooGPIOSamsungLunarLake::interrupt(OSObject *owner, IOInterruptEventSource *, int) {
    VoodooGPIOSamsungLunarLake *controller = static_cast<VoodooGPIOSamsungLunarLake *>(owner);
    controller->retain();
    controller->dispatch();
    controller->release();
}
void VoodooGPIOSamsungLunarLake::dispatch() {
    for (unsigned index = 0; index < 51; ++index) {
        if (!ready || !controllerIsAwake || !registers || !mapping) return;
        Registration &entry = registrations[index];
        LunarBridgeGPIO::Pin pin;
        if (!entry.enabled || !entry.target || !LunarBridgeGPIO::pin(layout, index, mapping->getLength(), pin)) continue;
        const UInt32 bit = UInt32(1) << pin.bit;
        if (!(readl(registers + pin.interruptStatus) & readl(registers + pin.interruptEnable) & bit)) continue;
        if (!eligible(pin)) {
            entry.enabled = false;
            ready = false; // A changed owner/mux requires controller teardown before another opt-in.
            interruptSource->disable();
            ++ownershipFaults;
            setProperty("LunarBridgeGPIOOwnershipFaults", ownershipFaults, 64);
            IOLog("LunarBridgeGPIO: ownership/mux changed; IRQ delivery stopped\n");
            return;
        }
        // Acknowledge only our pin; no foreign firmware status/enable bits are cleared.
        writel(bit, registers + pin.interruptStatus);
        OSObject *target = entry.target;
        IOInterruptAction action = entry.action;
        void *refcon = entry.refcon;
        // Count before the callback so a reentrant Close publishes the last delivery.
        if (!(++delivered & 63U)) setProperty("LunarBridgeGPIOIRQCallbacks", delivered, 64);
        target->retain();
        action(target, refcon, this, index);
        target->release();
        // Callback/release may Close, unregister clients or power down. Never reuse entry.
        if (!ready || !controllerIsAwake || !registers || !mapping) return;
    }
}
IOReturn VoodooGPIOSamsungLunarLake::getInterruptType(int number, int *type) {
    Command request {GetType, number, 0, nullptr, nullptr, type}; return command(request);
}
IOReturn VoodooGPIOSamsungLunarLake::setInterruptTypeForPin(int pin, int type) {
    Command request {SetType, pin, static_cast<unsigned>(type), nullptr, nullptr, nullptr}; return command(request);
}
IOReturn VoodooGPIOSamsungLunarLake::registerInterrupt(int pin, OSObject *target, IOInterruptAction handler, void *refcon) {
    Command request {Register, pin, 0, target, handler, refcon}; return command(request);
}
IOReturn VoodooGPIOSamsungLunarLake::unregisterInterrupt(int pin) {
    Command request {Unregister, pin, 0, nullptr, nullptr, nullptr}; return command(request);
}
IOReturn VoodooGPIOSamsungLunarLake::enableInterrupt(int pin) {
    Command request {Enable, pin, 0, nullptr, nullptr, nullptr}; return command(request);
}
IOReturn VoodooGPIOSamsungLunarLake::disableInterrupt(int pin) {
    Command request {Disable, pin, 0, nullptr, nullptr, nullptr}; return command(request);
}
IOReturn VoodooGPIOSamsungLunarLake::setPowerState(unsigned long state, IOService *device) {
    if (device == this && command_gate) {
        Command request {Power, 0, state ? 1U : 0U, nullptr, nullptr, nullptr}; command(request);
    }
    return kIOPMAckImplied;
}
void VoodooGPIOSamsungLunarLake::cleanup() {
    if (powerRegistered) { PMstop(); powerRegistered = false; }
    if (command_gate && gateAdded) {
        Command request {Close, 0, 0, nullptr, nullptr, nullptr}; command(request);
    } else ready = false;
    if (interruptSource) {
        interruptSource->disable();
        if (workLoop && interruptAdded) workLoop->removeEventSource(interruptSource);
        interruptAdded = false;
        OSSafeReleaseNULL(interruptSource);
    }
    if (command_gate) {
        if (workLoop && gateAdded) workLoop->removeEventSource(command_gate);
        gateAdded = false;
        OSSafeReleaseNULL(command_gate);
    }
    OSSafeReleaseNULL(workLoop);
    OSSafeReleaseNULL(mapping);
    registers = 0;
    controllerIsAwake = false;
}
void VoodooGPIOSamsungLunarLake::stop(IOService *provider) { cleanup(); IOService::stop(provider); }
void VoodooGPIOSamsungLunarLake::free() { cleanup(); IOService::free(); }
